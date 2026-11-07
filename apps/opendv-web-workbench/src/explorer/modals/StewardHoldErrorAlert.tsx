import { EXPLORER_FONTS } from "../design-tokens";

export interface ProblemDetails {
	type?: string | undefined;
	title?: string | undefined;
	status?: number | undefined;
	detail?: string | undefined;
	instance?: string | undefined;
	[key: string]: unknown;
}

export function parseProblemDetails(err: unknown): ProblemDetails {
	if (typeof err === "object" && err !== null) {
		const obj = err as Record<string, unknown>;
		if (obj.problemDetails && typeof obj.problemDetails === "object") {
			return obj.problemDetails as ProblemDetails;
		}
		if (typeof obj.title === "string" || typeof obj.detail === "string") {
			return {
				title:
					typeof obj.title === "string" ? obj.title : "Hold Resolution Failed",
				detail:
					typeof obj.detail === "string"
						? obj.detail
						: String(obj.message ?? "An error occurred"),
				status: typeof obj.status === "number" ? obj.status : 400,
				type: typeof obj.type === "string" ? obj.type : undefined,
			};
		}
		if (typeof obj.message === "string") {
			try {
				const parsed = JSON.parse(obj.message);
				if (typeof parsed === "object" && parsed !== null) {
					const p = parsed as Record<string, unknown>;
					return {
						title:
							typeof p.title === "string" ? p.title : "Hold Resolution Failed",
						detail:
							typeof p.detail === "string"
								? p.detail
								: typeof p.message === "string"
									? p.message
									: obj.message,
						status: typeof p.status === "number" ? p.status : 400,
						type: typeof p.type === "string" ? p.type : undefined,
					};
				}
			} catch {
				// not JSON string
			}
			return {
				title: "Hold Resolution Error",
				detail: obj.message,
				status: typeof obj.status === "number" ? obj.status : 500,
			};
		}
	}
	return { title: "Hold Resolution Error", detail: String(err), status: 500 };
}

export function StewardHoldErrorAlert({
	problemError,
}: { problemError: ProblemDetails }) {
	return (
		<div
			data-testid="problem-details-error"
			role="alert"
			style={{
				padding: "10px",
				borderRadius: "6px",
				background: "#FDECEA",
				border: "1px solid #F5C2C7",
				color: "#8F1D12",
				fontSize: "12px",
				display: "flex",
				flexDirection: "column",
				gap: "2px",
			}}
		>
			<div
				style={{
					display: "flex",
					justifyContent: "space-between",
					alignItems: "center",
				}}
			>
				<strong data-testid="problem-title">
					{problemError.title ?? "Error"}
				</strong>
				{problemError.status && (
					<span
						data-testid="problem-status"
						style={{
							fontFamily: EXPLORER_FONTS.mono,
							fontSize: "10px",
							padding: "1px 6px",
							borderRadius: "4px",
							background: "#8F1D12",
							color: "#FFFFFF",
						}}
					>
						HTTP {problemError.status}
					</span>
				)}
			</div>
			<div data-testid="problem-detail">
				{problemError.detail ?? "Submission failed."}
			</div>
			{problemError.type && (
				<div
					data-testid="problem-type"
					style={{
						fontFamily: EXPLORER_FONTS.mono,
						fontSize: "10px",
						color: "#6B7280",
					}}
				>
					Type: {problemError.type}
				</div>
			)}
		</div>
	);
}
