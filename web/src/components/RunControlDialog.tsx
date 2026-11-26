import { useEffect, useId, useRef, useState } from "react";
import { createProblemDetails, parseProblemDetails } from "../api/client";
import type {
	ProblemDetails,
	RunControlReq,
	RunStateResp,
} from "../api/openapi";
import { useProjectStore } from "../store/projectStore";
import { RemediationList } from "./RemediationList";

export type RunControlMode = "pause" | "hint" | "raise-budget";

export interface RunControlDialogProps {
	open: boolean;
	onClose: () => void;
	run: RunStateResp;
	initialMode?: RunControlMode;
	initialHint?: string;
}

export function RunControlDialog({
	open,
	onClose,
	run,
	initialMode = "hint",
	initialHint = "",
}: RunControlDialogProps) {
	const dialogRef = useRef<HTMLDialogElement>(null);
	const titleId = useId();
	const [mode, setMode] = useState<RunControlMode>(initialMode);
	const [hint, setHint] = useState(initialHint);
	const [extraTurns, setExtraTurns] = useState(4);
	const [extraTokens, setExtraTokens] = useState(100_000);
	const [extraTimeout, setExtraTimeout] = useState(300);
	const [busy, setBusy] = useState(false);
	const [problem, setProblem] = useState<ProblemDetails | null>(null);

	useEffect(() => {
		if (!open) return;
		setMode(initialMode);
		setHint(initialHint);
		setProblem(null);
	}, [open, initialMode, initialHint]);

	useEffect(() => {
		const el = dialogRef.current;
		if (!el) return;
		if (open && !el.open) el.showModal();
		else if (!open && el.open) el.close();
		const onClick = (e: MouseEvent) => {
			if (e.target === el) onClose();
		};
		el.addEventListener("click", onClick);
		return () => el.removeEventListener("click", onClick);
	}, [open, onClose]);

	const isDraining = run.status === "HOLD_REQUESTED";
	const isTerminal = run.status === "COMPLETED" || run.status === "FAILED";
	const canPauseOrHint =
		!isTerminal &&
		!isDraining &&
		run.status !== "HOLD_CHECKPOINTED" &&
		run.status !== "UNCERTIFIED_DRAFT";
	const canRaise = !isTerminal && !isDraining;
	const nTurns = Math.trunc(extraTurns);
	const nTokens = Math.trunc(extraTokens);
	const nTimeout = Math.trunc(extraTimeout);
	const hasBudgetDelta = nTurns > 0 || nTokens > 0 || nTimeout > 0;
	const actionDisabled =
		busy ||
		(mode === "raise-budget" ? !canRaise || !hasBudgetDelta : !canPauseOrHint);

	const submit = async (e: React.FormEvent) => {
		e.preventDefault();
		if (actionDisabled) return;
		if (mode === "hint" && !hint.trim()) {
			setProblem(
				createProblemDetails(
					"SYNTAX_INVALID_ARGUMENT",
					"Enter guidance text before sending a hint.",
					{ title: "Hint Required", run_id: run.run_id },
				),
			);
			return;
		}
		setBusy(true);
		setProblem(null);
		const req: RunControlReq =
			mode === "pause"
				? { action: "pause" }
				: mode === "hint"
					? { action: "hint", hint: hint.trim() }
					: {
							action: "raise-budget",
							extra_turns: nTurns > 0 ? nTurns : undefined,
							extra_tokens: nTokens > 0 ? nTokens : undefined,
							extra_timeout_seconds: nTimeout > 0 ? nTimeout : undefined,
						};
		try {
			await useProjectStore
				.getState()
				.runControlAction(run.step, run.run_id, req);
			onClose();
		} catch (err) {
			setProblem(parseProblemDetails(err));
		} finally {
			setBusy(false);
		}
	};

	return (
		<dialog
			ref={dialogRef}
			className="dialog-panel"
			aria-labelledby={titleId}
			onCancel={(e) => {
				e.preventDefault();
				onClose();
			}}
		>
			<form className="dialog-body stack-md" onSubmit={submit}>
				<div className="row-between">
					<div>
						<h2 id={titleId} className="dialog-title">
							Run Controls · {run.run_id}
						</h2>
						<p className="text-muted text-sm">
							{run.step.toUpperCase()} · Status: <strong>{run.status}</strong>
						</p>
					</div>
					<button
						type="button"
						className="btn btn-ghost btn-sm"
						onClick={onClose}
						aria-label="Close run controls"
					>
						Close
					</button>
				</div>

				{isDraining && (
					<div className="banner banner-warn">
						Draining active work... Pause, hint, and budget edits wait until the
						active checkpoint settles.
					</div>
				)}

				<fieldset
					className="radio-group"
					disabled={busy || isDraining || isTerminal}
				>
					<legend className="text-sm font-semibold">Action</legend>
					<label className="radio-row">
						<input
							type="radio"
							name="run_control_mode"
							checked={mode === "hint"}
							onChange={() => setMode("hint")}
							disabled={!canPauseOrHint}
						/>
						<span>Send Hint to Running Agent</span>
					</label>
					<label className="radio-row">
						<input
							type="radio"
							name="run_control_mode"
							checked={mode === "pause"}
							onChange={() => setMode("pause")}
							disabled={!canPauseOrHint}
						/>
						<span>Pause at Next Checkpoint</span>
					</label>
					<label className="radio-row">
						<input
							type="radio"
							name="run_control_mode"
							checked={mode === "raise-budget"}
							onChange={() => setMode("raise-budget")}
							disabled={!canRaise}
						/>
						<span>Raise Run Budget (+turns / +tokens / +seconds)</span>
					</label>
				</fieldset>

				{mode === "hint" && (
					<label className="field-col">
						<span className="field-label">Steward Hint</span>
						<textarea
							rows={3}
							className="input"
							value={hint}
							onChange={(e) => setHint(e.target.value)}
							placeholder="Clarify key authority, table scope, or naming constraint..."
						/>
					</label>
				)}

				{mode === "raise-budget" && (
					<div className="grid-3">
						<label className="field-col">
							<span className="field-label">+Turns</span>
							<input
								type="number"
								min={0}
								max={500}
								className="input"
								value={extraTurns}
								onChange={(e) => setExtraTurns(Number(e.target.value) || 0)}
							/>
						</label>
						<label className="field-col">
							<span className="field-label">+Tokens</span>
							<input
								type="number"
								min={0}
								step={10000}
								className="input"
								value={extraTokens}
								onChange={(e) => setExtraTokens(Number(e.target.value) || 0)}
							/>
						</label>
						<label className="field-col">
							<span className="field-label">+Timeout (s)</span>
							<input
								type="number"
								min={0}
								max={7200}
								step={60}
								className="input"
								value={extraTimeout}
								onChange={(e) => setExtraTimeout(Number(e.target.value) || 0)}
							/>
						</label>
					</div>
				)}

				{problem && (
					<div className="banner banner-danger stack-sm" role="alert">
						<div>
							<strong>{problem.title}</strong> ({problem.error_code}):{" "}
							{problem.detail}
						</div>
						<RemediationList remediation={problem.remediation} />
					</div>
				)}

				<div className="row-end gap-sm">
					<button type="button" className="btn btn-secondary" onClick={onClose}>
						Cancel
					</button>
					<button
						type="submit"
						className="btn btn-primary"
						disabled={actionDisabled}
					>
						{busy
							? "Applying..."
							: mode === "pause"
								? "Request Pause"
								: mode === "hint"
									? "Send Hint"
									: "Apply Budget Increase"}
					</button>
				</div>
			</form>
		</dialog>
	);
}
