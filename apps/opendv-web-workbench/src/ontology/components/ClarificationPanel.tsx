/**
 * Clarification Q&A Panel & Answer Submissions (Task 5D.3, #224)
 * (apps/opendv-web-workbench/src/ontology/components/ClarificationPanel.tsx)
 *
 * Implements:
 * - Stack of domain ambiguity question cards from @domain-clarifier
 * - Candidate Proposals summary cards with one-click right-panel inspection
 * - Human steward ratification controls delegated to shared workspace components
 */

import type React from "react";
import { useState } from "react";
import { useStudioStore } from "../store/studio-store";
import type { ClarificationAnswer } from "../store/types";
import {
	ClarificationQuestionsList,
	ProposalRatificationControls,
} from "./ProposalWorkspaceShared";

export interface ClarificationPanelProps {
	className?: string;
	onAnswerSubmitted?: (answer: ClarificationAnswer) => void;
}

export const ClarificationPanel: React.FC<ClarificationPanelProps> = ({
	className = "",
	onAnswerSubmitted,
}) => {
	const activeSession = useStudioStore((s) => s.activeSession);
	const userRole = useStudioStore((s) => s.userRole);
	const clarifications = useStudioStore((s) => s.clarifications);
	const proposals = useStudioStore((s) => s.proposals);
	const isLoadingClarifications = useStudioStore(
		(s) => s.isLoadingClarifications,
	);
	const isClarifying = useStudioStore((s) => s.isClarifying);
	const isConstructing = useStudioStore((s) => s.isConstructing);
	const isLoadingProposals = useStudioStore((s) => s.isLoadingProposals);
	const selectedProposalId = useStudioStore((s) => s.selectedProposalId);
	const loadClarifications = useStudioStore((s) => s.loadClarifications);
	const startClarify = useStudioStore((s) => s.startClarify);
	const startConstruct = useStudioStore((s) => s.startConstruct);
	const loadProposals = useStudioStore((s) => s.loadProposals);
	const selectProposal = useStudioStore((s) => s.selectProposal);
	const setRightDrawerTab = useStudioStore((s) => s.setRightDrawerTab);
	const acceptProposal = useStudioStore((s) => s.acceptProposal);
	const rejectProposal = useStudioStore((s) => s.rejectProposal);
	const rebaseProposal = useStudioStore((s) => s.rebaseProposal);

	const [proposalRationales, setProposalRationales] = useState<
		Record<string, string>
	>({});
	const [proposalSelfOverrides, setProposalSelfOverrides] = useState<
		Record<string, boolean>
	>({});

	const pendingCount = clarifications.filter(
		(q) => q.status === "PENDING",
	).length;
	const answeredCount = clarifications.filter(
		(q) => q.status === "ANSWERED",
	).length;
	const canHumanRatify = userRole === "editor" || userRole === "admin";
	const canTriggerJobs = userRole !== "viewer";

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
					<div className="panel-badges">
						<button
							type="button"
							className="btn-secondary btn-xs"
							onClick={() => setRightDrawerTab("proposal")}
							disabled={!activeSession}
							data-testid="open-proposal-workspace-btn"
						>
							Manage in Right Panel
						</button>
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
				</div>
				{proposals.length > 0 && (
					<div className="proposals-list" data-testid="proposals-list">
						{proposals.map((prop) => {
							const isNonPending = Boolean(
								prop.status && prop.status !== "PENDING",
							);
							const customAcceptRationale =
								proposalRationales[prop.proposal_id] || "";
							const selfAcceptOverride = Boolean(
								proposalSelfOverrides[prop.proposal_id],
							);
							const opsCount = Array.isArray(prop.operations)
								? prop.operations.length
								: (prop.operations_count ?? prop.ops_count ?? 0);
							const hasZeroOps = opsCount === 0;
							return (
								<div
									key={prop.proposal_id}
									className={`proposal-card${selectedProposalId === prop.proposal_id ? " proposal-card-selected" : ""}`}
									data-testid={`proposal-card-${prop.proposal_id}`}
								>
									<div className="proposal-card-header">
										<strong>{prop.proposal_id}</strong>
										<div className="panel-badges">
											<span
												className="badge"
												data-testid={`proposal-ops-badge-${prop.proposal_id}`}
											>
												{opsCount} ops
											</span>
											<span className="badge">{prop.status}</span>
										</div>
									</div>
									{prop.rationale && (
										<p className="proposal-rationale">{prop.rationale}</p>
									)}
									{hasZeroOps && !isNonPending && (
										<p
											className="proposal-empty-notice"
											data-testid={`proposal-empty-notice-${prop.proposal_id}`}
										>
											0 operations — upload and parse source documents first
										</p>
									)}
									<ProposalRatificationControls
										proposal={prop}
										userRole={userRole}
										showInspectButton
										acceptLabel="Accept"
										rationaleValue={customAcceptRationale}
										selfOverrideChecked={selfAcceptOverride}
										onRationaleChange={(val) =>
											setProposalRationales((prev) => ({
												...prev,
												[prop.proposal_id]: val,
											}))
										}
										onSelfOverrideChange={(checked) =>
											setProposalSelfOverrides((prev) => ({
												...prev,
												[prop.proposal_id]: checked,
											}))
										}
										onInspect={() => selectProposal(prop.proposal_id)}
										onAccept={() =>
											void acceptProposal(
												prop.proposal_id,
												customAcceptRationale.trim() || undefined,
												undefined,
												userRole === "admin" && selfAcceptOverride
													? { selfAcceptanceOverride: true }
													: undefined,
											)
										}
										onRebase={() => void rebaseProposal(prop.proposal_id)}
										onReject={() => void rejectProposal(prop.proposal_id)}
									/>
								</div>
							);
						})}
					</div>
				)}
			</div>

			<ClarificationQuestionsList
				canHumanRatify={canHumanRatify}
				onAnswerSubmitted={onAnswerSubmitted}
			/>
		</div>
	);
};
