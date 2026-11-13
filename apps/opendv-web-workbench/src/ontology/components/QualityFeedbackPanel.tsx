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
import { useEffect, useMemo, useRef, useState } from "react";
import { deriveGroundingStatus, useStudioStore } from "../store/studio-store";
import type { ExternalMapping, QualityCheck } from "../store/types";

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
	const proposals = useStudioStore((s) => s.proposals);
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
	const [excludedWaiverUris, setExcludedWaiverUris] = useState<string[]>([]);
	const [waiverError, setWaiverError] = useState<string | null>(null);
	const [isSubmittingWaiver, setIsSubmittingWaiver] = useState(false);

	// Feedback state
	const [rating, setRating] = useState(qualityScorecard.feedback_rating || 0);
	const [hoverRating, setHoverRating] = useState(0);
	const [comment, setComment] = useState(
		qualityScorecard.feedback_comment || "",
	);
	const [feedbackConceptUri, setFeedbackConceptUri] = useState<string>(
		qualityScorecard.feedback_concept_uri || "",
	);
	const [isSubmittingFeedback, setIsSubmittingFeedback] = useState(false);

	const ungroundedConcepts = useMemo(() => {
		const seenUris = new Set<string>();
		const items: Array<{ concept_uri: string; concept_name: string }> = [];

		for (const n of nodes) {
			const hasMappings =
				Array.isArray(n.external_mappings) && n.external_mappings.length > 0;
			const status = hasMappings
				? deriveGroundingStatus(n.external_mappings, n.confidence)
						.grounding_status
				: "UNGROUNDED";
			if (status !== "GROUNDED" && !seenUris.has(n.concept_uri)) {
				seenUris.add(n.concept_uri);
				items.push({
					concept_uri: n.concept_uri,
					concept_name: n.concept_name,
				});
			}
		}

		for (const prop of proposals) {
			if (prop.status && prop.status.toUpperCase() !== "PENDING") {
				continue;
			}
			for (const op of prop.operations || []) {
				if (
					op &&
					typeof op === "object" &&
					op.op_type === "add_concept" &&
					typeof op.concept_uri === "string" &&
					op.concept_uri.trim().length > 0
				) {
					const uri = op.concept_uri.trim();
					if (seenUris.has(uri)) continue;
					const rawMappings = Array.isArray(op.external_mappings)
						? (op.external_mappings as ExternalMapping[])
						: [];
					const rawConf =
						typeof op.confidence === "number" ? op.confidence : undefined;
					const status =
						rawMappings.length > 0
							? deriveGroundingStatus(rawMappings, rawConf).grounding_status
							: "UNGROUNDED";
					if (status !== "GROUNDED") {
						seenUris.add(uri);
						const rawName =
							typeof op.concept_name === "string" && op.concept_name.trim()
								? op.concept_name.trim()
								: uri.split(":").pop()?.replace(/_/g, " ") || uri;
						items.push({
							concept_uri: uri,
							concept_name: rawName,
						});
					}
				}
			}
		}

		return items;
	}, [nodes, proposals]);

	const selectedWaiverUris = useMemo(
		() =>
			ungroundedConcepts
				.map((n) => n.concept_uri)
				.filter((uri) => !excludedWaiverUris.includes(uri)),
		[ungroundedConcepts, excludedWaiverUris],
	);

	const activeWaivedSet = useMemo(
		() => new Set(qualityScorecard.waived_concept_uris || []),
		[qualityScorecard.waived_concept_uris],
	);

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
			setFeedbackConceptUri(qualityScorecard.feedback_concept_uri || "");
			setExcludedWaiverUris([]);
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

		if (ungroundedConcepts.length > 0 && selectedWaiverUris.length === 0) {
			setWaiverError(
				"Select at least one ungrounded concept to receive the administrative waiver.",
			);
			return;
		}

		setIsSubmittingWaiver(true);
		setWaiverError(null);
		try {
			const ok = await submitWaiver(
				waiverRationale.trim(),
				waiverKey.trim(),
				selectedWaiverUris,
			);
			if (ok) {
				setWaiverKey("");
				if (onWaiverSubmitted) {
					onWaiverSubmitted(waiverRationale.trim());
				}
			} else {
				setWaiverError(
					"Waiver submission rejected by server or preconditions not met.",
				);
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
			const ok = await submitFeedback(
				rating,
				comment.trim(),
				feedbackConceptUri || null,
			);
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
	const groundedNodes = nodes.filter((n) => {
		const hasMappings =
			Array.isArray(n.external_mappings) && n.external_mappings.length > 0;
		return (
			hasMappings &&
			deriveGroundingStatus(n.external_mappings, n.confidence)
				.grounding_status === "GROUNDED"
		);
	}).length;
	const groundingPct = hasNodes
		? qualityScorecard.grounding_score !== undefined
			? qualityScorecard.grounding_score * 100
			: (groundedNodes / nodes.length) * 100
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
						✓ Active Waiver Authorized ({qualityScorecard.waiver_rationale}) —{" "}
						{(qualityScorecard.waived_concept_uris || []).length} concept(s)
						covered
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
						<span>Request Per-Concept Grounding Waiver</span>
					</label>

					{waiverRequested && (
						<div className="waiver-fields">
							{ungroundedConcepts.length > 0 && (
								<div
									className="form-group waiver-concepts-group"
									data-testid="waiver-concepts-list"
								>
									<span className="form-label">
										Target Ungrounded Concepts ({selectedWaiverUris.length}/
										{ungroundedConcepts.length}):
									</span>
									{ungroundedConcepts.map((concept) => {
										const checked = selectedWaiverUris.includes(
											concept.concept_uri,
										);
										const isCurrentlyWaived = activeWaivedSet.has(
											concept.concept_uri,
										);
										return (
											<label
												key={concept.concept_uri}
												className="checkbox-sm waiver-concept-row"
											>
												<input
													type="checkbox"
													checked={checked}
													onChange={(e) => {
														if (e.target.checked) {
															setExcludedWaiverUris((prev) =>
																prev.filter((u) => u !== concept.concept_uri),
															);
														} else {
															setExcludedWaiverUris((prev) => [
																...prev,
																concept.concept_uri,
															]);
														}
													}}
													data-testid={`waiver-concept-checkbox-${concept.concept_uri}`}
												/>
												<span>
													{concept.concept_name} (
													<code>{concept.concept_uri}</code>)
												</span>
												{isCurrentlyWaived && (
													<span
														className="badge badge-pass"
														data-testid={`waiver-active-pill-${concept.concept_uri}`}
													>
														WAIVED
													</span>
												)}
											</label>
										);
									})}
								</div>
							)}
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
						<label htmlFor="feedback-concept-select">
							Target Concept (Optional):
						</label>
						<select
							id="feedback-concept-select"
							className="form-select"
							value={feedbackConceptUri}
							onChange={(e) => setFeedbackConceptUri(e.target.value)}
							data-testid="feedback-concept-select"
						>
							<option value="">Session-wide Feedback</option>
							{nodes.map((n) => (
								<option key={n.concept_uri} value={n.concept_uri}>
									{n.concept_name} ({n.concept_uri})
								</option>
							))}
						</select>
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
