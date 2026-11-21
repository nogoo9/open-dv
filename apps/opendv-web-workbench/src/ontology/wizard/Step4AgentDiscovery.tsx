/**
 * Step 4: Agent Discovery, 422 Coral Gate, Cascading Cherry-Pick & Citations (`P2`, `D1..D7`, `D27`, `D28`)
 * (apps/opendv-web-workbench/src/ontology/wizard/Step4AgentDiscovery.tsx)
 */

import type React from "react";
import { CLAUDE_TOKENS, EXPLORER_FONTS } from "../../explorer/design-tokens";
import { safeUseState } from "../../explorer/modals/safeHooks";
import { AgentHarnessConfigCard } from "../../explorer/settings/AgentHarnessConfigCard";
import {
	type PlatformConfigResponse,
	isActiveProviderReady,
} from "../../explorer/settings/usePlatformConfigActions";
import type {
	BudgetHoldInfo,
	ClarificationAnswer,
	ClarificationQuestion,
} from "../store/types";
import { AskFiboPanel, type AskFiboSuggestedAction } from "./AskFiboPanel";
import { BudgetHoldCard } from "./BudgetHoldCard";
import {
	type WizardBreakingCandidate,
	type WizardConceptCandidate,
	cascadeUncheckConcept,
} from "./wizard-types";

export { cascadeUncheckConcept };

export interface Step4AgentDiscoveryProps {
	sessionId?: string | null | undefined;
	platformConfig?: PlatformConfigResponse | null | undefined;
	agentErrorCode?: string | null | undefined;
	agentErrorDetail?: string | null | undefined;
	steeringPrompt?: string | undefined;
	concepts?: WizardConceptCandidate[] | undefined;
	breakingCandidates?: WizardBreakingCandidate[] | undefined;
	clarifications?: ClarificationQuestion[] | undefined;
	activeSessionJobId?: string | null | undefined;
	isRunningPipeline?: boolean | undefined;
	budgetHold?: BudgetHoldInfo | null | undefined;
	askFiboAnswer?: string | null | undefined;
	askFiboCitations?: Array<Record<string, unknown>> | undefined;
	askFiboActions?: AskFiboSuggestedAction[] | undefined;
	onPlatformConfigUpdated?:
		| ((config: PlatformConfigResponse, ready: boolean) => void)
		| undefined;
	onRunDiscovery?:
		| ((steeringPrompt: string) => Promise<void> | void)
		| undefined;
	onCancelJob?: (() => Promise<void> | void) | undefined;
	onRaiseBudgetAndResume?: (() => Promise<void> | void) | undefined;
	onAbortBudgetHold?: (() => Promise<void> | void) | undefined;
	onSubmitClarification?:
		| ((answer: ClarificationAnswer) => Promise<boolean> | undefined)
		| undefined;
	onToggleConcept?:
		| ((candidateId: string, checked: boolean) => void)
		| undefined;
	onUpdateAttrType?:
		| ((candidateId: string, attrId: string, dataType: string) => void)
		| undefined;
	onToggleBreaking?:
		| ((candidateId: string, checked: boolean) => void)
		| undefined;
	onInspectCitations?:
		| ((refs: Array<Record<string, unknown>>) => void)
		| undefined;
	onAskFibo?: ((question: string) => Promise<void> | void) | undefined;
	onApplyAskFiboAction?: ((action: AskFiboSuggestedAction) => void) | undefined;
	onOpenLogs?:
		| ((opts?: { stageId?: string; wizardStep?: number }) => void)
		| undefined;
}

export const Step4AgentDiscovery: React.FC<Step4AgentDiscoveryProps> = ({
	sessionId,
	platformConfig,
	agentErrorCode,
	agentErrorDetail,
	steeringPrompt: initialSteering = "",
	concepts = [],
	breakingCandidates = [],
	clarifications = [],
	activeSessionJobId,
	isRunningPipeline = false,
	budgetHold,
	askFiboAnswer,
	askFiboCitations = [],
	askFiboActions = [],
	onPlatformConfigUpdated,
	onRunDiscovery,
	onCancelJob,
	onRaiseBudgetAndResume,
	onAbortBudgetHold,
	onSubmitClarification,
	onToggleConcept,
	onUpdateAttrType,
	onToggleBreaking,
	onInspectCitations,
	onAskFibo,
	onApplyAskFiboAction,
	onOpenLogs,
}) => {
	const [steering, setSteering] = safeUseState(initialSteering);
	const [selectedOptByQ, setSelectedOptByQ] = safeUseState<
		Record<string, string>
	>({});
	const [customTextByQ, setCustomTextByQ] = safeUseState<
		Record<string, string>
	>({});
	const [submittingQ, setSubmittingQ] = safeUseState<string | null>(null);
	const needsAgentGate =
		agentErrorCode === "AGENT_NOT_CONFIGURED" ||
		(platformConfig != null && !isActiveProviderReady(platformConfig).ready);

	const structuredConcepts = concepts.filter(
		(c) =>
			c.bucket === "structured" || c.grounding_origin === "STRUCTURED_SCHEMA",
	);
	const documentConcepts = concepts.filter(
		(c) =>
			c.bucket !== "structured" && c.grounding_origin !== "STRUCTURED_SCHEMA",
	);

	if (needsAgentGate) {
		return (
			<section
				data-testid="step4-agent-setup-gate"
				style={{ display: "flex", flexDirection: "column", gap: "16px" }}
			>
				<div
					className="claude-card"
					style={{
						padding: "16px 20px",
						borderLeft: `4px solid ${CLAUDE_TOKENS.colors.primary}`,
						background: "#fdf2ee",
					}}
				>
					<div
						style={{
							display: "flex",
							alignItems: "center",
							gap: "8px",
							marginBottom: "6px",
						}}
					>
						<span
							className="claude-pill"
							style={{
								background: CLAUDE_TOKENS.colors.primary,
								color: "#fff",
							}}
						>
							422 · AGENT_NOT_CONFIGURED
						</span>
						<strong
							style={{ fontSize: "14px", color: CLAUDE_TOKENS.colors.ink }}
						>
							Configure Agent Harness Before Discovery
						</strong>
					</div>
					<p
						style={{
							margin: 0,
							fontSize: "13px",
							color: CLAUDE_TOKENS.colors.body,
						}}
					>
						{agentErrorDetail ||
							"An LLM agent provider must be configured and verified before running Step 4 ontology discovery."}
					</p>
				</div>

				<AgentHarnessConfigCard
					variant="gate"
					initialConfig={platformConfig ?? undefined}
					onVerifiedAndSaved={(updated, ready) => {
						onPlatformConfigUpdated?.(updated, ready);
					}}
				/>
			</section>
		);
	}

	const renderConceptCard = (c: WizardConceptCandidate) => (
		<article
			key={c.candidate_id}
			className={c.checked ? "claude-card-selected" : "claude-card"}
			style={{
				padding: "12px",
				display: "flex",
				flexDirection: "column",
				gap: "8px",
			}}
		>
			<div
				style={{
					display: "flex",
					justifyContent: "space-between",
					alignItems: "center",
					gap: "8px",
				}}
			>
				<label
					style={{
						display: "flex",
						alignItems: "center",
						gap: "8px",
						fontWeight: 600,
						fontSize: "13.5px",
					}}
				>
					<input
						type="checkbox"
						checked={c.checked}
						onChange={(e) =>
							onToggleConcept?.(c.candidate_id, e.target.checked)
						}
					/>
					<span>{c.concept_name}</span>
				</label>
				<span className="claude-pill" style={{ fontSize: "11px" }}>
					{c.mention_count === 1 || c.bucket === "unstructured"
						? "1x · Semantic"
						: "Structured"}
				</span>
			</div>

			{c.fibo_match && (
				<div
					style={{
						fontSize: "11.5px",
						color: CLAUDE_TOKENS.colors.body,
						fontFamily: EXPLORER_FONTS.mono,
					}}
				>
					<span>{c.fibo_match.predicate}</span> ·{" "}
					{(c.fibo_match.score * 100).toFixed(0)}%
				</div>
			)}

			{c.attributes.length > 0 && (
				<div style={{ display: "flex", flexDirection: "column", gap: "4px" }}>
					{c.attributes.map((attr) => (
						<div
							key={attr.attribute_id}
							style={{
								display: "flex",
								justifyContent: "space-between",
								alignItems: "center",
								gap: "6px",
								fontSize: "12px",
							}}
						>
							<span>{attr.name}</span>
							<input
								type="text"
								aria-label={`Data type for ${attr.name}`}
								value={attr.data_type}
								onChange={(e) =>
									onUpdateAttrType?.(
										c.candidate_id,
										attr.attribute_id,
										e.target.value,
									)
								}
								style={{
									width: "120px",
									padding: "2px 6px",
									fontFamily: EXPLORER_FONTS.mono,
									fontSize: "11px",
									borderRadius: "4px",
									border: `1px solid ${CLAUDE_TOKENS.colors.hairline}`,
								}}
							/>
						</div>
					))}
				</div>
			)}

			{c.evidence_refs && c.evidence_refs.length > 0 && (
				<button
					type="button"
					className="claude-btn-secondary"
					style={{
						fontSize: "11px",
						padding: "3px 8px",
						alignSelf: "flex-start",
					}}
					onClick={() => onInspectCitations?.(c.evidence_refs ?? [])}
				>
					Inspect Evidence ({c.evidence_refs.length})
				</button>
			)}
		</article>
	);

	return (
		<section
			data-testid="wizard-step-4"
			style={{ display: "flex", flexDirection: "column", gap: "18px" }}
		>
			<header
				style={{
					display: "flex",
					justifyContent: "space-between",
					alignItems: "flex-start",
					gap: "12px",
					flexWrap: "wrap",
				}}
			>
				<div>
					<h1 className="claude-h1" style={{ margin: "0 0 6px" }}>
						Step 4 · Agentic Ontology Discovery &amp; Grounding
					</h1>
					<div style={{ display: "flex", gap: "6px", flexWrap: "wrap" }}>
						<span className="claude-pill">@term-scout</span>
						<span className="claude-pill">@domain-clarifier</span>
						<span className="claude-pill">@ontology-architect</span>
					</div>
				</div>
				<button
					type="button"
					className="claude-btn-secondary"
					data-testid="btn-step4-view-logs"
					onClick={() => onOpenLogs?.({ wizardStep: 4 })}
				>
					View Stage Logs ↗
				</button>
			</header>

			<div
				className="claude-card-cream"
				style={{
					padding: "14px",
					display: "flex",
					gap: "10px",
					alignItems: "center",
					flexWrap: "wrap",
				}}
			>
				<input
					type="text"
					data-testid="input-steering-prompt"
					value={steering}
					onChange={(e) => setSteering(e.target.value)}
					placeholder="Optional steering instructions for ontology discovery..."
					style={{
						flex: 1,
						minWidth: "240px",
						padding: "8px 12px",
						borderRadius: "6px",
						border: `1px solid ${CLAUDE_TOKENS.colors.hairlineStrong}`,
						background: CLAUDE_TOKENS.colors.canvas,
						color: CLAUDE_TOKENS.colors.ink,
					}}
				/>
				<button
					type="button"
					className="claude-btn-primary"
					data-testid="btn-run-agent-discovery"
					disabled={Boolean(activeSessionJobId || isRunningPipeline)}
					onClick={() => onRunDiscovery?.(steering)}
				>
					{activeSessionJobId || isRunningPipeline
						? "Run in progress — view live logs"
						: "Run Agent Discovery"}
				</button>
				{activeSessionJobId && (
					<button
						type="button"
						className="claude-btn-secondary"
						data-testid="btn-cancel-active-job"
						onClick={() => onCancelJob?.()}
					>
						Cancel Active Job
					</button>
				)}
			</div>

			<BudgetHoldCard
				budgetHold={budgetHold}
				onRaiseBudgetAndResume={onRaiseBudgetAndResume}
				onAbortBudgetHold={onAbortBudgetHold}
			/>

			<div
				className="claude-responsive-grid"
				style={{
					display: "grid",
					gridTemplateColumns: "2.2fr 1fr",
					gap: "16px",
					alignItems: "start",
				}}
			>
				<div
					className="claude-responsive-grid"
					style={{
						minWidth: 0,
						display: "grid",
						gridTemplateColumns: "repeat(3, minmax(0, 1fr))",
						gap: "12px",
					}}
				>
					<div
						style={{
							minWidth: 0,
							display: "flex",
							flexDirection: "column",
							gap: "10px",
						}}
					>
						<h3 style={{ margin: 0, fontSize: "13px", fontWeight: 600 }}>
							Structured Schema ({structuredConcepts.length})
						</h3>
						{structuredConcepts.map(renderConceptCard)}
					</div>

					<div
						style={{
							minWidth: 0,
							display: "flex",
							flexDirection: "column",
							gap: "10px",
						}}
					>
						<h3 style={{ margin: 0, fontSize: "13px", fontWeight: 600 }}>
							Document-Observed ({documentConcepts.length})
						</h3>
						{documentConcepts.map(renderConceptCard)}
					</div>

					<div
						style={{
							minWidth: 0,
							display: "flex",
							flexDirection: "column",
							gap: "10px",
						}}
					>
						<h3 style={{ margin: 0, fontSize: "13px", fontWeight: 600 }}>
							Breaking Opt-In &amp; Clarifications
						</h3>
						{breakingCandidates.map((bk) => (
							<label
								key={bk.candidate_id}
								className="claude-card"
								style={{
									padding: "10px 12px",
									display: "flex",
									justifyContent: "space-between",
									alignItems: "center",
									gap: "8px",
									borderColor: "#d4a017",
								}}
							>
								<span
									style={{
										display: "flex",
										alignItems: "center",
										gap: "6px",
										fontSize: "12.5px",
									}}
								>
									<input
										type="checkbox"
										checked={bk.checked}
										onChange={(e) =>
											onToggleBreaking?.(bk.candidate_id, e.target.checked)
										}
									/>
									{bk.label}
								</span>
								<span className="claude-pill" style={{ fontSize: "10.5px" }}>
									MAJOR · Opt-In
								</span>
							</label>
						))}

						{clarifications.map((q) => {
							type OptionLike = {
								option_id?: string | undefined;
								id?: string | undefined;
								label: string;
							};
							const rawOpts =
								(
									q as ClarificationQuestion & {
										options?: OptionLike[] | undefined;
									}
								).options ??
								q.rationale_options ??
								[];
							const opts = rawOpts as OptionLike[];
							const selectedOpt =
								selectedOptByQ[q.question_id] ??
								q.selected_option_ids?.[0] ??
								"";
							const customTxt =
								customTextByQ[q.question_id] ?? q.custom_answer_text ?? "";
							const allowCustom =
								(
									q as ClarificationQuestion & {
										allow_custom_text?: boolean | undefined;
									}
								).allow_custom_text !== false;
							const isBusy = submittingQ === q.question_id;
							return (
								<div
									key={q.question_id}
									data-testid={`clarification-card-${q.question_id}`}
									className="claude-card"
									style={{
										padding: "10px 12px",
										fontSize: "12.5px",
										display: "flex",
										flexDirection: "column",
										gap: "6px",
									}}
								>
									<div
										style={{
											display: "flex",
											justifyContent: "space-between",
											alignItems: "center",
											gap: "6px",
										}}
									>
										<span style={{ fontWeight: 600 }}>
											{q.focus_concept ?? q.concept_name}
										</span>
										{q.status === "ANSWERED" && (
											<span
												className="claude-pill"
												style={{ fontSize: "10px" }}
											>
												ANSWERED
											</span>
										)}
									</div>
									<p style={{ margin: "2px 0" }}>{q.question_text}</p>
									{opts.map((opt, idx) => {
										const optId = opt.option_id ?? opt.id ?? `opt-${idx}`;
										return (
											<label
												key={optId}
												style={{
													display: "flex",
													alignItems: "center",
													gap: "6px",
													fontSize: "11.5px",
													color: CLAUDE_TOKENS.colors.ink,
													cursor: "pointer",
												}}
											>
												<input
													type="radio"
													name={`clarify-${q.question_id}`}
													value={optId}
													checked={selectedOpt === optId}
													onChange={() =>
														setSelectedOptByQ({
															...selectedOptByQ,
															[q.question_id]: optId,
														})
													}
												/>
												<span>{opt.label}</span>
											</label>
										);
									})}
									{allowCustom && (
										<input
											type="text"
											data-testid={`input-clarification-custom-${q.question_id}`}
											placeholder="Custom answer (optional)..."
											value={customTxt}
											onChange={(e) =>
												setCustomTextByQ({
													...customTextByQ,
													[q.question_id]: e.target.value,
												})
											}
											style={{
												padding: "4px 8px",
												borderRadius: "4px",
												border: `1px solid ${CLAUDE_TOKENS.colors.hairline}`,
												fontSize: "11.5px",
											}}
										/>
									)}
									<button
										type="button"
										className="claude-btn-secondary"
										data-testid={`btn-submit-clarification-${q.question_id}`}
										disabled={isBusy || (!selectedOpt && !customTxt.trim())}
										style={{
											fontSize: "11px",
											padding: "4px 8px",
											alignSelf: "flex-start",
										}}
										onClick={async () => {
											if (!onSubmitClarification) return;
											setSubmittingQ(q.question_id);
											try {
												await onSubmitClarification({
													question_id: q.question_id,
													selected_option_ids: selectedOpt ? [selectedOpt] : [],
													...(customTxt.trim()
														? { custom_answer_text: customTxt.trim() }
														: {}),
												});
											} finally {
												setSubmittingQ(null);
											}
										}}
									>
										{isBusy ? "Submitting..." : "Submit Answer"}
									</button>
								</div>
							);
						})}
					</div>
				</div>

				<div style={{ minWidth: 0 }}>
					<AskFiboPanel
						sessionId={sessionId}
						answer={askFiboAnswer}
						citations={askFiboCitations}
						suggestedActions={askFiboActions}
						disabledReason={
							activeSessionJobId || isRunningPipeline
								? "Run in progress — Ask FIBO pauses while session job is active."
								: null
						}
						onAsk={onAskFibo}
						onApplySuggestedAction={onApplyAskFiboAction}
						onInspectCitation={(cit) => onInspectCitations?.([cit])}
					/>
				</div>
			</div>
		</section>
	);
};
