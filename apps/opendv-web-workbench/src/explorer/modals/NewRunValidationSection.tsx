import { EXPLORER_COLORS, EXPLORER_FONTS } from "../design-tokens";

export interface ValidationDiagnostic {
	code?: string | undefined;
	severity: "INFO" | "WARNING" | "ERROR";
	target?: string | undefined;
	message: string;
}

export interface PipelineValidationReport {
	stage?: string | undefined;
	passed: boolean;
	checked_items_count: number;
	diagnostics: ValidationDiagnostic[];
}

export interface NewRunValidationSectionProps {
	isValidating: boolean;
	onValidate: () => void;
	validationReport?: PipelineValidationReport | null | undefined;
}

export function NewRunValidationSection({
	isValidating,
	onValidate,
	validationReport,
}: NewRunValidationSectionProps) {
	return (
		<div style={{ display: "flex", flexDirection: "column", gap: "8px" }}>
			<button
				type="button"
				data-testid="btn-validate-pipeline"
				onClick={onValidate}
				disabled={isValidating}
				style={{
					padding: "6px 12px",
					fontSize: "12px",
					fontWeight: 500,
					border: `1px solid ${EXPLORER_COLORS.borderLine}`,
					borderRadius: "4px",
					background: EXPLORER_COLORS.bgSubtle,
					color: EXPLORER_COLORS.inkPrimary,
					cursor: isValidating ? "wait" : "pointer",
					alignSelf: "flex-start",
				}}
			>
				{isValidating ? "Validating..." : "Pre-flight Validate"}
			</button>

			{validationReport && (
				<div
					data-testid="validation-diagnostics"
					style={{
						padding: "10px",
						borderRadius: "6px",
						backgroundColor: validationReport.passed ? "#F0FDF4" : "#FEF2F2",
						border: `1px solid ${validationReport.passed ? "#BBF7D0" : "#FECACA"}`,
						display: "flex",
						flexDirection: "column",
						gap: "6px",
						fontSize: "12px",
					}}
				>
					<div
						style={{
							display: "flex",
							alignItems: "center",
							justifyContent: "space-between",
						}}
					>
						<span
							data-testid="validation-status"
							style={{
								fontWeight: 600,
								color: validationReport.passed ? "#166534" : "#991B1B",
							}}
						>
							{validationReport.passed ? "VALID" : "INVALID"}
						</span>
						<span
							data-testid="validation-checked-count"
							style={{
								fontSize: "11px",
								color: EXPLORER_COLORS.inkMuted,
							}}
						>
							{validationReport.checked_items_count} items checked
						</span>
					</div>

					{validationReport.diagnostics.map((diag, idx) => (
						<div
							key={
								diag.code
									? `${diag.code}-${idx}`
									: `diag-${diag.severity}-${idx}`
							}
							data-testid="diagnostic-item"
							style={{
								fontFamily: EXPLORER_FONTS.mono,
								fontSize: "11px",
								color:
									diag.severity === "ERROR"
										? "#991B1B"
										: diag.severity === "WARNING"
											? "#B45309"
											: "#166534",
							}}
						>
							[{diag.severity}] {diag.code ? `${diag.code}: ` : ""}
							{diag.message}
							{diag.target ? ` (${diag.target})` : ""}
						</div>
					))}
				</div>
			)}
		</div>
	);
}
