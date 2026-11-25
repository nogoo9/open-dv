import { useEffect, useId, useRef, useState } from "react";
import type {
	GuidedDecisionCard as GuidedDecisionCardDTO,
	GuidedWaiverTargetSpec,
	HorizonActionReq,
	RunStateResp,
	WaiverSubmitReq,
} from "../api/openapi";
import { useProjectStore } from "../store/projectStore";

export interface GuidedDecisionCardProps {
	card: GuidedDecisionCardDTO;
	run: RunStateResp | null;
	v6bTarget?: GuidedWaiverTargetSpec | null;
	waveNumber: number;
	step4RunId?: string | null;
	isMobile?: boolean;
	busy?: boolean;
	onSubmitDecision: (answers: Record<string, string>) => Promise<void>;
	onSubmitHorizon: (req: HorizonActionReq) => Promise<void>;
	onSubmitWaiver: (req: WaiverSubmitReq) => Promise<void>;
	onAskAiRefactor: (card: GuidedDecisionCardDTO) => Promise<void>;
	onNavigateStep4: (step4RunId: string | null) => void;
}

export function GuidedDecisionCard({
	card,
	run,
	v6bTarget,
	waveNumber,
	step4RunId = null,
	isMobile = false,
	busy = false,
	onSubmitDecision,
	onSubmitHorizon,
	onSubmitWaiver,
	onAskAiRefactor,
	onNavigateStep4,
}: GuidedDecisionCardProps) {
	const setDirtyFlag = useProjectStore((s) => s.setDirtyFlag);
	const titleId = useId();
	const dlgRef = useRef<HTMLDialogElement>(null);

	const [selectedOpt, setSelectedOpt] = useState(
		card.recommended_option_id || card.options[0]?.option_id || "",
	);
	const [customHoldAnswer, setCustomHoldAnswer] = useState("");
	const [guidanceOpen, setGuidanceOpen] = useState(false);
	const [notes, setNotes] = useState("");
	const [addedFieldsRaw, setAddedFieldsRaw] = useState("");
	const [waiverOpen, setWaiverOpen] = useState(false);
	const [waiverRationale, setWaiverRationale] = useState("");

	useEffect(() => {
		setSelectedOpt(
			card.recommended_option_id || card.options[0]?.option_id || "",
		);
	}, [card.recommended_option_id, card.options]);

	useEffect(() => {
		const el = dlgRef.current;
		if (!el) return;
		if (waiverOpen && !el.open) el.showModal();
		else if (!waiverOpen && el.open) {
			el.returnValue = "";
			el.close();
		}
		const onClick = (e: MouseEvent) => {
			if (e.target === el && !waiverRationale.trim()) setWaiverOpen(false);
		};
		el.addEventListener("click", onClick);
		return () => el.removeEventListener("click", onClick);
	}, [waiverOpen, waiverRationale]);

	const isDraining = run?.status === "HOLD_REQUESTED";

	const markDirtyIfEdited = (
		nextCustom: string,
		nextNotes: string,
		nextFields: string,
		nextWaiverRat: string,
	) => {
		setDirtyFlag(
			"step6Decisions",
			Boolean(
				nextCustom.trim() ||
					nextNotes.trim() ||
					nextFields.trim() ||
					nextWaiverRat.trim(),
			),
		);
	};

	const submitHold = async () => {
		const val = customHoldAnswer.trim() || selectedOpt;
		if (!val || busy || isDraining) return;
		await onSubmitDecision({ [card.card_id]: val });
		setCustomHoldAnswer("");
		setDirtyFlag("step6Decisions", false);
	};

	const submitHorizon = async () => {
		if (!selectedOpt || busy || isDraining) return;
		const added_fields = addedFieldsRaw
			.split(",")
			.map((s) => s.trim())
			.filter(Boolean);
		const isAccept =
			selectedOpt === card.recommended_option_id &&
			notes.trim().length === 0 &&
			added_fields.length === 0;
		await onSubmitHorizon({
			action: isAccept ? "accept" : "steer",
			answers: { [card.card_id]: selectedOpt },
			notes: notes.trim(),
			added_fields,
		});
		setNotes("");
		setAddedFieldsRaw("");
		setDirtyFlag("step6Decisions", false);
	};

	const submitWaiver = async (e: React.FormEvent) => {
		e.preventDefault();
		if (!v6bTarget || waiverRationale.trim().length < 20 || busy) return;
		await onSubmitWaiver({
			waiver_id: v6bTarget.waiver_id,
			rule_id: v6bTarget.rule_id,
			entity_name: v6bTarget.entity_name,
			rationale: waiverRationale.trim(),
		});
		setWaiverRationale("");
		setWaiverOpen(false);
		setDirtyFlag("step6Decisions", false);
	};

	return (
		<article className="card-selected stack-sm" aria-labelledby={titleId}>
			<div className="row-between">
				<h3 id={titleId} className="h2-serif">
					{card.title}
				</h3>
				<div className="row">
					<span className="pill pill-warn">{card.lock_in_option_label}</span>
					<span className="pill pill-sal">{card.flexible_option_label}</span>
				</div>
			</div>

			<p>{card.plain_summary}</p>
			<div className="callout-amber text-sm">
				<strong>Why It Matters:</strong> {card.why_it_matters}
			</div>

			{(card.sample_evidence ?? []).length > 0 && (
				<div className="card-subtle stack-sm">
					<div className="field-label">Sample Evidence</div>
					{(card.sample_evidence ?? []).map((row, idx) => (
						<div
							key={`${card.card_id}-ev-${idx}`}
							className="card-cream text-sm"
						>
							{row}
						</div>
					))}
				</div>
			)}

			{card.source_step === "step4" && (
				<div className="row-end">
					<button
						type="button"
						className="btn btn-primary"
						onClick={() => onNavigateStep4(step4RunId)}
					>
						Return to Step 4{step4RunId ? ` (${step4RunId})` : ""}
					</button>
				</div>
			)}

			{(card.source_step === "step6_hold" ||
				card.source_step === "step6_horizon") && (
				<div className="stack-sm">
					<fieldset className="radio-group" disabled={busy || isDraining}>
						<legend className="field-label">Choose Business Direction</legend>
						{card.options.map((opt) => {
							const isRec = opt.option_id === card.recommended_option_id;
							return (
								<label key={opt.option_id} className="radio-row">
									<input
										type="radio"
										name={`card-${card.card_id}`}
										value={opt.option_id}
										checked={selectedOpt === opt.option_id}
										onChange={() => setSelectedOpt(opt.option_id)}
									/>
									<div className="stack-sm" style={{ gap: 2 }}>
										<div className="row">
											<strong>{opt.label}</strong>
											<span
												className={isRec ? "pill pill-ok" : "pill pill-warn"}
											>
												{isRec ? "Recommended" : "High Lock-In Risk"}
											</span>
										</div>
										<span className="text-sm">{opt.description}</span>
										<span className="text-sm text-muted">
											{opt.implications}
										</span>
									</div>
								</label>
							);
						})}
					</fieldset>

					{card.source_step === "step6_hold" && (
						<div className="stack-sm">
							<label className="field-col">
								<span className="field-label">
									Optional Custom Business Clarification
								</span>
								<input
									type="text"
									className="input"
									disabled={busy || isDraining}
									placeholder="Or write a custom answer..."
									value={customHoldAnswer}
									onChange={(e) => {
										setCustomHoldAnswer(e.target.value);
										markDirtyIfEdited(
											e.target.value,
											notes,
											addedFieldsRaw,
											waiverRationale,
										);
									}}
								/>
							</label>
							<div className="row-end">
								<button
									type="button"
									className="btn btn-primary"
									disabled={
										busy ||
										isDraining ||
										(!selectedOpt && !customHoldAnswer.trim())
									}
									onClick={() => void submitHold()}
								>
									Submit Choice &amp; Resume →
								</button>
							</div>
						</div>
					)}

					{card.source_step === "step6_horizon" && (
						<div className="stack-sm">
							<div>
								<button
									type="button"
									className="btn btn-sm btn-secondary"
									aria-expanded={guidanceOpen}
									onClick={() => setGuidanceOpen((v) => !v)}
								>
									{guidanceOpen
										? "Hide Optional Business Guidance Note"
										: "Optional Business Guidance Note"}
								</button>
							</div>
							{guidanceOpen && (
								<div className="card-subtle stack-sm">
									<label className="field-col">
										<span className="field-label">Business Guidance Note</span>
										<textarea
											rows={2}
											className="textarea"
											disabled={busy || isDraining}
											placeholder="Optional plain-English note for future waves..."
											value={notes}
											onChange={(e) => {
												setNotes(e.target.value);
												markDirtyIfEdited(
													customHoldAnswer,
													e.target.value,
													addedFieldsRaw,
													waiverRationale,
												);
											}}
										/>
									</label>
									<label className="field-col">
										<span className="field-label">
											Additional Fields to Track (comma-separated)
										</span>
										<input
											type="text"
											className="input"
											disabled={busy || isDraining}
											placeholder="e.g. custodian_bic, effective_date"
											value={addedFieldsRaw}
											onChange={(e) => {
												setAddedFieldsRaw(e.target.value);
												markDirtyIfEdited(
													customHoldAnswer,
													notes,
													e.target.value,
													waiverRationale,
												);
											}}
										/>
									</label>
								</div>
							)}
							<div className="row-end">
								<button
									type="button"
									className="btn btn-primary"
									disabled={busy || isDraining || !selectedOpt}
									onClick={() => void submitHorizon()}
								>
									{isMobile
										? "Confirm Business Choice →"
										: `Confirm Business Choice & Certify Wave ${waveNumber} →`}
								</button>
							</div>
						</div>
					)}
				</div>
			)}

			{card.source_step === "step6_v6b" && (
				<div className="row-end">
					<button
						type="button"
						className="btn btn-secondary"
						disabled={busy || isDraining}
						onClick={() => void onAskAiRefactor(card)}
					>
						Ask AI to Refactor Instead
					</button>
					<button
						type="button"
						className="btn btn-primary"
						disabled={busy || isDraining || !v6bTarget}
						onClick={() => setWaiverOpen(true)}
					>
						Review &amp; Sign Business Waiver
					</button>
				</div>
			)}

			{card.source_step === "step6_v6b" && (
				<dialog
					ref={dlgRef}
					className="dialog-panel"
					aria-labelledby={`${titleId}-waiver`}
					{...({ closedby: "closerequest" } as Record<string, string>)}
					onCancel={(e) => {
						e.preventDefault();
						setWaiverOpen(false);
					}}
				>
					<form className="stack-md" onSubmit={submitWaiver}>
						<div className="row-between">
							<h3 id={`${titleId}-waiver`} className="dialog-title">
								Sign Business Exception Waiver
							</h3>
							<button
								type="button"
								className="btn btn-ghost btn-sm"
								onClick={() => setWaiverOpen(false)}
							>
								Close
							</button>
						</div>
						{v6bTarget && (
							<div className="card-cream text-sm stack-sm">
								<div>
									<strong>Target Entity:</strong> {v6bTarget.entity_name}
								</div>
								<div>
									<strong>Server Reference:</strong> {v6bTarget.waiver_id}
								</div>
							</div>
						)}
						<label className="field-col">
							<span className="field-label">
								Business Rationale (&ge; 20 characters required)
							</span>
							<textarea
								rows={3}
								className="textarea"
								value={waiverRationale}
								placeholder="Explain why this business design choice is intentional..."
								onChange={(e) => {
									setWaiverRationale(e.target.value);
									markDirtyIfEdited(
										customHoldAnswer,
										notes,
										addedFieldsRaw,
										e.target.value,
									);
								}}
							/>
						</label>
						<div className="row-end">
							<button
								type="button"
								className="btn btn-secondary"
								onClick={() => setWaiverOpen(false)}
							>
								Cancel
							</button>
							<button
								type="submit"
								className="btn btn-primary"
								disabled={
									busy || !v6bTarget || waiverRationale.trim().length < 20
								}
							>
								Submit Signed Waiver
							</button>
						</div>
					</form>
				</dialog>
			)}
		</article>
	);
}
