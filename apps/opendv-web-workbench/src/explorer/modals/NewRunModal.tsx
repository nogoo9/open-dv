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
import type { ChangeEvent } from "react";
import type { StagedPipelineSpec } from "../builder/specBuilderUtils";
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

export type { StewardHoldPolicyOption, StagedPipelineSpec };
export type { PipelineValidationReport, ValidationDiagnostic };

export interface NewRunModalProps {
	isOpen: boolean;
	onClose: () => void;
	onRunStarted?: ((handle: PipelineRunHandle) => void) | undefined;
	onOpenBuilderView?: (() => void) | undefined;
	stagedSpec?: StagedPipelineSpec | null | undefined;
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
	onOpenBuilderView,
	stagedSpec,
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
	const [specPath, setSpecPath] = safeUseState(
		stagedSpec?.inputSpecPath ?? defaultSpecPath,
	);
	const [specFileName, setSpecFileName] = safeUseState<string | undefined>(
		stagedSpec?.fileName,
	);
	const [specFileContent, setSpecFileContent] = safeUseState<
		string | undefined
	>(stagedSpec?.inputSpecContent);

	const [contractPath, setContractPath] = safeUseState(
		stagedSpec?.initialContractPath ?? defaultContractPath,
	);
	const [contractFileName, setContractFileName] = safeUseState<
		string | undefined
	>(undefined);
	const [contractFileContent, setContractFileContent] = safeUseState<
		string | undefined
	>(stagedSpec?.initialContractContent);

	const [waveId, setWaveId] = safeUseState(stagedSpec?.waveId ?? defaultWaveId);
	const [holdPolicy, setHoldPolicy] = safeUseState<StewardHoldPolicyOption>(
		stagedSpec?.stewardHoldPolicy ?? defaultHoldPolicy,
	);

	const [isValidating, setIsValidating] = safeUseState(false);
	const [validationReport, setValidationReport] =
		safeUseState<PipelineValidationReport | null>(
			initialValidationReport ?? null,
		);

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
				inputSpec: stagedSpec?.inputSpec,
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
				specFileContent,
				inputSpec: stagedSpec?.inputSpec,
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
				<form
					onSubmit={(e) => e.preventDefault()}
					style={{
						padding: "20px",
						display: "flex",
						flexDirection: "column",
						gap: "16px",
					}}
				>
					{stagedSpec && (
						<div
							data-testid="modal-staged-spec-banner"
							style={{
								padding: "8px 12px",
								borderRadius: "6px",
								backgroundColor: "#F0FDF4",
								border: "1px solid #BBF7D0",
								fontSize: "12px",
								color: "#166534",
								display: "flex",
								justifyContent: "space-between",
								alignItems: "center",
							}}
						>
							<span>✓ Staged specification ready ({stagedSpec.mode} mode)</span>
						</div>
					)}

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
				</form>

				{/* Modal Footer */}
				<footer
					style={{
						padding: "14px 20px",
						borderTop: `1px solid ${EXPLORER_COLORS.borderLine}`,
						display: "flex",
						justifyContent: "space-between",
						alignItems: "center",
						gap: "10px",
					}}
				>
					<button
						type="button"
						data-testid="btn-modal-open-builder"
						onClick={() => {
							onClose();
							onOpenBuilderView?.();
						}}
						style={{
							padding: "6px 12px",
							fontSize: "12px",
							fontWeight: 500,
							border: `1px solid ${EXPLORER_COLORS.borderLine}`,
							borderRadius: "4px",
							background: EXPLORER_COLORS.bgSubtle,
							color: EXPLORER_COLORS.inkPrimary,
							cursor: "pointer",
						}}
					>
						Interactive Builder
					</button>
					<div style={{ display: "flex", gap: "10px" }}>
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
					</div>
				</footer>
			</div>
		</div>
	);
}
