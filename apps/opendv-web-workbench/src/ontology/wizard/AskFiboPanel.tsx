/**
 * Dual FIBO & Sources Q&A Side Rail Panel (`D2`, `/fibo-qa`, `FR-CIT-04`)
 * (apps/opendv-web-workbench/src/ontology/wizard/AskFiboPanel.tsx)
 */

import type React from "react";
import { CLAUDE_TOKENS, EXPLORER_FONTS } from "../../explorer/design-tokens";
import { safeUseState } from "../../explorer/modals/safeHooks";

export interface AskFiboSuggestedAction {
	action_id: string;
	label: string;
	op_type: string;
	payload?: Record<string, unknown> | undefined;
}

export interface AskFiboPanelProps {
	sessionId?: string | null | undefined;
	question?: string | undefined;
	answer?: string | null | undefined;
	citations?: Array<Record<string, unknown>> | undefined;
	suggestedActions?: AskFiboSuggestedAction[] | undefined;
	isAsking?: boolean | undefined;
	disabledReason?: string | null | undefined;
	onAsk?: ((question: string) => Promise<void> | void) | undefined;
	onInspectCitation?: ((citation: Record<string, unknown>) => void) | undefined;
	onApplySuggestedAction?:
		| ((action: AskFiboSuggestedAction) => void)
		| undefined;
}

export const AskFiboPanel: React.FC<AskFiboPanelProps> = ({
	sessionId,
	question: initialQuestion = "",
	answer,
	citations = [],
	suggestedActions = [],
	isAsking = false,
	disabledReason,
	onAsk,
	onInspectCitation,
	onApplySuggestedAction,
}) => {
	const [questionDraft, setQuestionDraft] = safeUseState(initialQuestion);

	const handleSubmit = async (e: React.FormEvent) => {
		e.preventDefault();
		const q = questionDraft.trim();
		if (!q || isAsking || disabledReason || !onAsk) return;
		await onAsk(q);
	};

	return (
		<aside
			data-testid="ask-fibo-panel"
			className="claude-card"
			aria-label="Ask FIBO and Attached Sources Q&A"
			style={{
				padding: "16px",
				display: "flex",
				flexDirection: "column",
				gap: "12px",
				background: CLAUDE_TOKENS.colors.surfaceSoft,
				border: `1px solid ${CLAUDE_TOKENS.colors.hairline}`,
			}}
		>
			<div
				style={{
					display: "flex",
					justifyContent: "space-between",
					alignItems: "center",
				}}
			>
				<div>
					<h3
						style={{
							margin: 0,
							fontFamily: EXPLORER_FONTS.serif,
							fontSize: "19px",
							fontWeight: 400,
							color: CLAUDE_TOKENS.colors.ink,
						}}
					>
						Ask FIBO &amp; Sources
					</h3>
					<p
						style={{
							margin: "2px 0 0",
							fontSize: "12px",
							color: CLAUDE_TOKENS.colors.muted,
						}}
					>
						Dual-grounding assistant against FIBO ontology &amp; parsed session
						evidence.
					</p>
				</div>
				{sessionId && (
					<span className="claude-pill" style={{ fontSize: "11px" }}>
						{sessionId}
					</span>
				)}
			</div>

			<form
				onSubmit={handleSubmit}
				style={{ display: "flex", flexDirection: "column", gap: "8px" }}
			>
				<textarea
					data-testid="ask-fibo-question-input"
					rows={2}
					placeholder="Ask about FIBO classes, SKOS alignment, or attached source columns..."
					value={questionDraft}
					onChange={(e) => setQuestionDraft(e.target.value)}
					style={{
						width: "100%",
						padding: "8px 10px",
						borderRadius: "6px",
						border: `1px solid ${CLAUDE_TOKENS.colors.hairlineStrong}`,
						background: CLAUDE_TOKENS.colors.canvas,
						color: CLAUDE_TOKENS.colors.ink,
						fontFamily: EXPLORER_FONTS.sans,
						fontSize: "13px",
						resize: "vertical",
					}}
				/>
				<button
					type="submit"
					className="claude-btn-primary"
					data-testid="btn-ask-fibo-submit"
					disabled={
						isAsking || Boolean(disabledReason) || !questionDraft.trim()
					}
				>
					{isAsking ? "Querying /fibo-qa..." : "Ask FIBO"}
				</button>
			</form>

			{disabledReason && (
				<div
					style={{
						fontSize: "12px",
						color: CLAUDE_TOKENS.colors.primaryActive,
						background: CLAUDE_TOKENS.colors.surfaceCreamStrong,
						padding: "8px 10px",
						borderRadius: "6px",
					}}
				>
					{disabledReason}
				</div>
			)}

			{answer && (
				<div
					data-testid="ask-fibo-answer"
					style={{
						padding: "12px",
						borderRadius: "8px",
						background: CLAUDE_TOKENS.colors.canvas,
						border: `1px solid ${CLAUDE_TOKENS.colors.hairline}`,
						fontSize: "13px",
						lineHeight: 1.55,
						color: CLAUDE_TOKENS.colors.ink,
					}}
				>
					<div>{answer}</div>
					{citations.length > 0 && (
						<div
							style={{
								display: "flex",
								flexWrap: "wrap",
								gap: "6px",
								marginTop: "10px",
							}}
						>
							{(() => {
								const seen = new Map<string, number>();
								return citations.map((cit) => {
									const base = String(
										cit.chunk_id ??
											cit.fibo_uri ??
											cit.source_label ??
											"Citation",
									);
									const count = (seen.get(base) ?? 0) + 1;
									seen.set(base, count);
									const label = String(
										cit.source_label ??
											cit.fibo_uri ??
											cit.chunk_id ??
											`Citation ${count}`,
									);
									return (
										<button
											key={`${base}#${count}`}
											type="button"
											className="claude-btn-secondary"
											style={{ fontSize: "11px", padding: "3px 8px" }}
											onClick={() => onInspectCitation?.(cit)}
										>
											{label}
										</button>
									);
								});
							})()}
						</div>
					)}
				</div>
			)}

			{suggestedActions.length > 0 && (
				<div style={{ display: "flex", flexDirection: "column", gap: "6px" }}>
					<span
						style={{
							fontSize: "11px",
							fontWeight: 600,
							textTransform: "uppercase",
							letterSpacing: "0.04em",
							color: CLAUDE_TOKENS.colors.muted,
						}}
					>
						Suggested Actions (1-Click)
					</span>
					{suggestedActions.map((act) => (
						<button
							key={act.action_id}
							type="button"
							className="claude-btn-secondary"
							data-testid={`ask-fibo-action-${act.action_id}`}
							style={{
								textAlign: "left",
								justifyContent: "space-between",
								display: "flex",
								fontSize: "12px",
							}}
							onClick={() => onApplySuggestedAction?.(act)}
						>
							<span>{act.label}</span>
							<span style={{ color: CLAUDE_TOKENS.colors.primary }}>Apply</span>
						</button>
					))}
				</div>
			)}
		</aside>
	);
};
