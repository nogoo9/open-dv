import type { PipelineRunHandle } from "@opendv/protocol-ts";
import { EXPLORER_FONTS } from "../design-tokens";

export interface NewRunStatusBannersProps {
	createdHandle?: PipelineRunHandle | null | undefined;
	submitError?: string | null | undefined;
}

export function NewRunStatusBanners({
	createdHandle,
	submitError,
}: NewRunStatusBannersProps) {
	return (
		<>
			{/* Created Run Banner */}
			{createdHandle && (
				<div
					data-testid="run-created-banner"
					style={{
						padding: "10px",
						borderRadius: "6px",
						backgroundColor: "#EFF6FF",
						border: "1px solid #BFDBFE",
						fontSize: "12px",
						color: "#1E40AF",
					}}
				>
					Run initiated:{" "}
					<code
						data-testid="created-run-id"
						style={{ fontFamily: EXPLORER_FONTS.mono, fontWeight: 600 }}
					>
						{createdHandle.run_id}
					</code>{" "}
					(Status: {createdHandle.status})
				</div>
			)}

			{/* Submit Error Banner */}
			{submitError && (
				<div
					data-testid="run-error-banner"
					style={{
						padding: "10px",
						borderRadius: "6px",
						backgroundColor: "#FEF2F2",
						border: "1px solid #FECACA",
						fontSize: "12px",
						color: "#991B1B",
					}}
				>
					{submitError}
				</div>
			)}
		</>
	);
}
