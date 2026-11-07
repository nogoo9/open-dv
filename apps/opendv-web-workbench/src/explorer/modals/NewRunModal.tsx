/**
 * New Pipeline Run Modal (`NewRunModal.tsx`)
 *
 * Async Run Creation Dialog conforming to Decision D2 (HTTP 202 Accepted):
 * - Input spec path or inline upload
 * - Initial contract path or inline upload (GAP-07)
 * - Target wave selector (All, W0, W1, W2, W3)
 * - Steward hold policy (pause, auto_approve_top_candidate, reject_on_hold)
 * - Pre-flight input validation action (`POST /api/v1/pipelines/validate`)
 * - Triggers async pipeline run (`POST /api/v1/pipelines/runs`) returning HTTP 202
 */

import type {
	PipelineRunHandle,
	PipelineRunRequest,
	PipelineValidateRequest,
} from "@opendv/protocol-ts";
import { type ChangeEvent } from "react";
import { EXPLORER_COLORS, EXPLORER_FONTS } from "../design-tokens";
import {
	NewRunFormFields,
	type StewardHoldPolicyOption,
} from "./NewRunFormFields";
import { NewRunStatusBanners } from "./NewRunStatusBanners";
import {
	NewRunValidationSection,
	type PipelineValidationReport,
	type ValidationDiagnostic,
} from "./NewRunValidationSection";
import { safeUseEffect, safeUseState } from "./safeHooks";
import { executeSubmitRun, executeValidate } from "./useNewRunActions";

export type { StewardHoldPolicyOption };
export type { PipelineValidationReport, ValidationDiagnostic };

export interface NewRunModalProps {
	isOpen: boolean;
	onClose: () => void;
	onRunStarted?: ((handle: PipelineRunHandle) => void) | undefined;
	fetchImpl?: typeof fetch | undefined;
	apiUrl?: string | undefined;
	defaultSpecPath?: string | undefined;
	defaultContractPath?: string | undefined;
	defaultWaveId?: string | undefined;
	defaultHoldPolicy?: StewardHoldPolicyOption | undefined;
	initialValidationReport?: PipelineValidationReport | null | undefined;
	initialCreatedHandle?: PipelineRunHandle | null | undefined;
	initialSubmitError?: string | null | undefined;
	createRunFn?:
		| ((req: PipelineRunRequest) => Promise<PipelineRunHandle>)
		| undefined;
	validatePipelineFn?:
		| ((req: PipelineValidateRequest) => Promise<PipelineValidationReport>)
		| undefined;
}

export function NewRunModal({
	isOpen,
	onClose,
	onRunStarted,
	fetchImpl,
	apiUrl,
	defaultSpecPath = "",
	defaultContractPath = "",
	defaultWaveId = "",
	defaultHoldPolicy = "pause",
	initialValidationReport,
	initialCreatedHandle,
	initialSubmitError,
	createRunFn,
	validatePipelineFn,
}: NewRunModalProps) {
	const [specPath, setSpecPath] = safeUseState(defaultSpecPath);
	const [specFileName, setSpecFileName] = safeUseState<string | undefined>(
		undefined,
	);
	const [specFileContent, setSpecFileContent] = safeUseState<
		string | undefined
	>(undefined);

	const [contractPath, setContractPath] = safeUseState(defaultContractPath);
	const [contractFileName, setContractFileName] = safeUseState<
		string | undefined
	>(undefined);
	const [contractFileContent, setContractFileContent] = safeUseState<
		string | undefined
	>(undefined);

	const [waveId, setWaveId] = safeUseState(defaultWaveId);
	const [holdPolicy, setHoldPolicy] =
		safeUseState<StewardHoldPolicyOption>(defaultHoldPolicy);

	const [isValidating, setIsValidating] = safeUseState(false);
	const [validationReport, setValidationReport] =
		safeUseState<PipelineValidationReport | null>(initialValidationReport ?? null);

	const [isSubmitting, setIsSubmitting] = safeUseState(false);
	const [createdHandle, setCreatedHandle] =
		safeUseState<PipelineRunHandle | null>(initialCreatedHandle ?? null);
	const [submitError, setSubmitError] = safeUseState<string | null>(
		initialSubmitError ?? null,
	);

	// Close on Escape key
	safeUseEffect(() => {
		if (!isOpen) return;
		const onKeyDown = (e: KeyboardEvent) => {
			if (e.key === "Escape") {
				e.preventDefault();
				onClose();
			}
		};
		if (
			typeof globalThis !== "undefined" &&
			typeof globalThis.addEventListener === "function"
		) {
			globalThis.addEventListener("keydown", onKeyDown);
			return () => {
				globalThis.removeEventListener("keydown", onKeyDown);
			};
		}
		return;
	}, [isOpen, onClose]);

	if (!isOpen) {
		return null;
	}

	const handleSpecFileUpload = (e: ChangeEvent<HTMLInputElement>) => {
		const file = e.target.files?.[0];
		if (!file) return;
		setSpecFileName(file.name);
		const reader = new FileReader();
		reader.onload = (evt) => {
			const text = evt.target?.result;
			if (typeof text === "string") {
				setSpecFileContent(text);
			}
		};
		reader.readAsText(file);
	};

	const handleContractFileUpload = (e: ChangeEvent<HTMLInputElement>) => {
		const file = e.target.files?.[0];
		if (!file) return;
		setContractFileName(file.name);
		const reader = new FileReader();
		reader.onload = (evt) => {
			const text = evt.target?.result;
			if (typeof text === "string") {
				setContractFileContent(text);
			}
		};
		reader.readAsText(file);
	};

	const handleValidate = async () => {
		setIsValidating(true);
		setSubmitError(null);
		try {
			const report = await executeValidate({
				specPath,
				specFileName,
				specFileContent,
				contractPath,
				contractFileContent,
				waveId,
				holdPolicy,
				apiUrl,
				fetchImpl,
				validatePipelineFn,
			});
			setValidationReport(report);
		} catch (err) {
			setValidationReport({
				stage: "INPUT_VALIDATION",
				passed: false,
				checked_items_count: 1,
				diagnostics: [
					{
						severity: "ERROR",
						message: err instanceof Error ? err.message : String(err),
					},
				],
			});
		} finally {
			setIsValidating(false);
		}
	};

	const handleSubmitRun = async () => {
		setIsSubmitting(true);
		setSubmitError(null);
		try {
			const handle = await executeSubmitRun({
				specPath,
				contractPath,
				contractFileContent,
				waveId,
				holdPolicy,
				apiUrl,
				fetchImpl,
				createRunFn,
			});
			setCreatedHandle(handle);
			onRunStarted?.(handle);
		} catch (err) {
			setSubmitError(err instanceof Error ? err.message : String(err));
		} finally {
			setIsSubmitting(false);
		}
	};

	return (
		<div
			role="dialog"
			aria-modal="true"
			aria-labelledby="new-run-modal-title"
			data-testid="new-run-modal"
			onClick={onClose}
			onKeyDown={(e) => {
				if (e.key === "Escape") onClose();
			}}
			style={{
				position: "fixed",
				inset: 0,
				zIndex: 2000,
				backgroundColor: "rgba(22, 24, 29, 0.45)",
				display: "flex",
				alignItems: "center",
				justifyContent: "center",
				padding: "16px",
			}}
		>
			<div
				onClick={(e) => e.stopPropagation()}
				style={{
					backgroundColor: EXPLORER_COLORS.bgSurface,
					borderRadius: "8px",
					border: `1px solid ${EXPLORER_COLORS.borderLine}`,
					width: "100%",
					maxWidth: "560px",
					maxHeight: "90vh",
					overflowY: "auto",
					boxShadow: "0 10px 25px rgba(0,0,0,0.15)",
					display: "flex",
					flexDirection: "column",
					fontFamily: EXPLORER_FONTS.sans,
					fontSize: "13px",
					color: EXPLORER_COLORS.inkPrimary,
				}}
			>
				{/* Modal Header */}
				<header
					style={{
						padding: "16px 20px",
						borderBottom: `1px solid ${EXPLORER_COLORS.borderLine}`,
						display: "flex",
						alignItems: "center",
						justifyContent: "space-between",
					}}
				>
					<div style={{ display: "flex", alignItems: "center", gap: "8px" }}>
						<span
							style={{
								width: "10px",
								height: "10px",
								background: EXPLORER_COLORS.glyphControl,
								borderRadius: "2px",
								clipPath: "polygon(50% 0,100% 50%,50% 100%,0 50%)",
							}}
						/>
						<h2
							id="new-run-modal-title"
							style={{ margin: 0, fontSize: "16px", fontWeight: 600 }}
						>
							New Pipeline Run
						</h2>
					</div>
					<button
						type="button"
						data-testid="btn-close-modal"
						aria-label="Close modal"
						onClick={onClose}
						style={{
							all: "unset",
							cursor: "pointer",
							fontSize: "18px",
							lineHeight: 1,
							padding: "4px 8px",
							color: EXPLORER_COLORS.inkMuted,
							borderRadius: "4px",
						}}
					>
						×
					</button>
				</header>

				{/* Modal Form Body */}
				<div
					style={{
						padding: "20px",
						display: "flex",
						flexDirection: "column",
						gap: "16px",
					}}
				>
					<NewRunFormFields
						specPath={specPath}
						onSpecPathChange={setSpecPath}
						specFileName={specFileName}
						onSpecFileClear={() => {
							setSpecFileName(undefined);
							setSpecFileContent(undefined);
						}}
						onSpecFileUpload={handleSpecFileUpload}
						contractPath={contractPath}
						onContractPathChange={setContractPath}
						contractFileName={contractFileName}
						onContractFileClear={() => {
							setContractFileName(undefined);
							setContractFileContent(undefined);
						}}
						onContractFileUpload={handleContractFileUpload}
						waveId={waveId}
						onWaveIdChange={setWaveId}
						holdPolicy={holdPolicy}
						onHoldPolicyChange={setHoldPolicy}
					/>

					<NewRunValidationSection
						isValidating={isValidating}
						onValidate={handleValidate}
						validationReport={validationReport}
					/>

					<NewRunStatusBanners
						createdHandle={createdHandle}
						submitError={submitError}
					/>
				</div>

				{/* Modal Footer */}
				<footer
					style={{
						padding: "14px 20px",
						borderTop: `1px solid ${EXPLORER_COLORS.borderLine}`,
						display: "flex",
						justifyContent: "flex-end",
						gap: "10px",
					}}
				>
					<button
						type="button"
						data-testid="btn-cancel-modal"
						onClick={onClose}
						style={{
							padding: "6px 12px",
							fontSize: "12.5px",
							border: `1px solid ${EXPLORER_COLORS.borderLine}`,
							borderRadius: "4px",
							background: EXPLORER_COLORS.bgSurface,
							color: EXPLORER_COLORS.inkMuted,
							cursor: "pointer",
						}}
					>
						Cancel
					</button>
					<button
						type="button"
						data-testid="btn-submit-run"
						onClick={handleSubmitRun}
						disabled={isSubmitting}
						style={{
							padding: "6px 14px",
							fontSize: "12.5px",
							fontWeight: 500,
							border: "none",
							borderRadius: "4px",
							background: EXPLORER_COLORS.inkPrimary,
							color: "#FFFFFF",
							cursor: isSubmitting ? "wait" : "pointer",
						}}
					>
						{isSubmitting ? "Starting Run..." : "Run Pipeline"}
					</button>
				</footer>
			</div>
		</div>
	);
}
