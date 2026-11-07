/**
 * Explicit Blocking Error Banner (`BlockingErrorBanner.tsx`)
 *
 * Rendered when the Open-DV Platform backend is unreachable or returns HTTP 502/504.
 * REQ-MOCK-01 & REQ-FAIL-01: Explicit failure state with zero demo data fallback.
 */

import type React from "react";
import { useRef } from "react";
import { EXPLORER_COLORS, EXPLORER_FONTS } from "./design-tokens";

export interface BlockingErrorState {
	hasError: boolean;
	title: string;
	endpoint: string;
	status?: number | undefined;
	details: string;
	retryAction: () => void;
	openFileAction: (file: File) => void;
}

export interface BlockingErrorBannerProps {
	error?: BlockingErrorState | undefined;
	title?: string | undefined;
	endpoint?: string | undefined;
	status?: number | undefined;
	details?: string | undefined;
	onRetry?: (() => void) | undefined;
	onOpenFile?: ((file: File) => void) | undefined;
}

export function BlockingErrorBanner({
	error,
	title,
	endpoint,
	status,
	details,
	onRetry,
	onOpenFile,
}: BlockingErrorBannerProps) {
	const fileInputRef = useRef<HTMLInputElement>(null);

	const effectiveTitle =
		error?.title ?? title ?? "Platform Backend Unreachable";
	const effectiveEndpoint = error?.endpoint ?? endpoint ?? "/api/v1/runs";
	const effectiveStatus = error?.status ?? status;
	const effectiveDetails =
		error?.details ??
		details ??
		"Unable to establish connection to the Open-DV platform API. The server may be offline or the proxy returned a gateway error.";
	const handleRetry = error?.retryAction ?? onRetry ?? (() => {});
	const handleOpenFile = error?.openFileAction ?? onOpenFile ?? (() => {});

	const onFileChange = (ev: React.ChangeEvent<HTMLInputElement>) => {
		const files = ev.target.files;
		if (files && files.length > 0 && files[0]) {
			handleOpenFile(files[0]);
		}
	};

	const triggerFileDialog = () => {
		fileInputRef.current?.click();
	};

	return (
		<div
			role="alert"
			aria-live="assertive"
			data-testid="blocking-error-banner"
			style={{
				display: "flex",
				flexDirection: "column",
				alignItems: "center",
				justifyContent: "center",
				minHeight: "380px",
				padding: "48px 24px",
				backgroundColor: "#FFFFFF",
				border: "1px solid #FCA5A5",
				borderRadius: "8px",
				margin: "24px auto",
				maxWidth: "680px",
				boxShadow: "0 4px 12px rgba(239, 68, 68, 0.08)",
				fontFamily: EXPLORER_FONTS.sans,
				color: EXPLORER_COLORS.inkPrimary,
				textAlign: "center",
			}}
		>
			{/* Error Icon Pill */}
			<div
				data-testid="blocking-error-icon"
				style={{
					display: "inline-flex",
					alignItems: "center",
					justifyContent: "center",
					width: "48px",
					height: "48px",
					borderRadius: "50%",
					backgroundColor: "#FEF2F2",
					border: "1px solid #F87171",
					color: "#DC2626",
					fontSize: "20px",
					fontWeight: 700,
					marginBottom: "16px",
				}}
			>
				!
			</div>

			{/* Title */}
			<h2
				data-testid="blocking-error-title"
				style={{
					fontSize: "18px",
					fontWeight: 600,
					color: "#991B1B",
					margin: "0 0 8px 0",
				}}
			>
				{effectiveTitle}
			</h2>

			{/* Status & Endpoint Metadata */}
			<div
				style={{
					display: "flex",
					alignItems: "center",
					justifyContent: "center",
					gap: "10px",
					marginBottom: "12px",
					fontFamily: EXPLORER_FONTS.mono,
					fontSize: "12px",
				}}
			>
				{effectiveStatus !== undefined && (
					<span
						data-testid="blocking-error-status"
						style={{
							backgroundColor: "#FEE2E2",
							color: "#991B1B",
							padding: "3px 8px",
							borderRadius: "4px",
							fontWeight: 600,
						}}
					>
						HTTP {effectiveStatus}
					</span>
				)}
				<span
					data-testid="blocking-error-endpoint"
					style={{
						backgroundColor: "#F3F4F6",
						color: "#374151",
						padding: "3px 8px",
						borderRadius: "4px",
					}}
				>
					{effectiveEndpoint}
				</span>
			</div>

			{/* Error Details */}
			<p
				data-testid="blocking-error-details"
				style={{
					fontSize: "13.5px",
					color: "#4B5563",
					maxWidth: "520px",
					lineHeight: 1.5,
					margin: "0 0 24px 0",
				}}
			>
				{effectiveDetails}
			</p>

			{/* Actions: Retry & Open Exported Run File */}
			<div
				style={{
					display: "flex",
					alignItems: "center",
					gap: "12px",
				}}
			>
				<button
					type="button"
					data-testid="btn-retry"
					onClick={handleRetry}
					style={{
						all: "unset",
						cursor: "pointer",
						padding: "8px 18px",
						backgroundColor: "#16181D",
						color: "#FFFFFF",
						borderRadius: "6px",
						fontSize: "13px",
						fontWeight: 500,
					}}
				>
					Retry Connection
				</button>

				<button
					type="button"
					data-testid="btn-open-file"
					onClick={triggerFileDialog}
					style={{
						all: "unset",
						cursor: "pointer",
						padding: "8px 18px",
						backgroundColor: "#FFFFFF",
						color: "#16181D",
						border: "1px solid #D1D5DB",
						borderRadius: "6px",
						fontSize: "13px",
						fontWeight: 500,
					}}
				>
					Open exported run...
				</button>

				<input
					ref={fileInputRef}
					type="file"
					accept=".json,.jsonl,.yaml,.yml"
					data-testid="file-input-exported-run"
					onChange={onFileChange}
					style={{ display: "none" }}
				/>
			</div>

			{/* Zero-Demo Invariant Notice */}
			<div
				style={{
					marginTop: "24px",
					fontSize: "11px",
					color: "#9CA3AF",
					fontFamily: EXPLORER_FONTS.mono,
				}}
			>
				REQ-MOCK-01: Zero demo data fallback. Real platform endpoint required.
			</div>
		</div>
	);
}
