import type { Stage } from "@opendv/protocol-ts";
import {
	EXPLORER_FONTS,
	getStatusIndicator,
	getStatusTagStyle,
} from "../design-tokens";

export interface StageHeaderProps {
	stage: Stage;
	stageIdx: number;
	onGoPipeline: () => void;
	onPrevStage: () => void;
	onNextStage: () => void;
}

export function StageHeader({
	stage,
	stageIdx,
	onGoPipeline,
	onPrevStage,
	onNextStage,
}: StageHeaderProps) {
	const numStr = String(stageIdx).padStart(2, "0");
	const tagStyle = getStatusTagStyle(stage.tag);
	const indicator = getStatusIndicator(stage.tag);

	return (
		<div
			onClick={(ev) => ev.stopPropagation()}
			style={{
				background: "#FFFFFF",
				border: "1px solid #E3E5E8",
				borderRadius: "8px",
				padding: "16px 20px",
				display: "flex",
				flexDirection: "column",
				gap: "10px",
			}}
		>
			<div
				style={{
					display: "flex",
					alignItems: "center",
					justifyContent: "space-between",
					gap: "12px",
				}}
			>
				<div style={{ display: "flex", alignItems: "center", gap: "12px" }}>
					<button
						type="button"
						data-testid="btn-stage-all-stages"
						onClick={onGoPipeline}
						aria-label="Back to all stages (Esc)"
						title="Back to all stages (Esc)"
						style={{
							all: "unset",
							cursor: "pointer",
							fontSize: "12.5px",
							fontWeight: 500,
							color: "#1D4ED8",
							padding: "4px 8px",
							borderRadius: "4px",
						}}
					>
						← All stages
					</button>
					<span style={{ color: "#D4D7DC" }}>|</span>
					<div
						style={{
							width: "34px",
							height: "34px",
							borderRadius: "999px",
							border: "1.5px solid #16181D",
							display: "flex",
							alignItems: "center",
							justifyContent: "center",
							fontFamily: EXPLORER_FONTS.mono,
							fontSize: "13px",
							fontWeight: 600,
						}}
					>
						{numStr}
					</div>
					<div>
						<div
							style={{
								display: "flex",
								alignItems: "center",
								gap: "10px",
							}}
						>
							<h1
								style={{
									margin: 0,
									fontSize: "20px",
									fontWeight: 600,
									letterSpacing: "-0.01em",
									color: "#16181D",
								}}
							>
								{stage.name}
							</h1>
							<span
								aria-label={`Status: ${stage.tag} (${indicator.label})`}
								style={{
									fontFamily: EXPLORER_FONTS.mono,
									fontSize: "11px",
									fontWeight: 500,
									padding: "2px 8px",
									borderRadius: "999px",
									background: tagStyle.bg,
									color: tagStyle.fg,
									display: "inline-flex",
									alignItems: "center",
									gap: "4px",
								}}
							>
								<span aria-hidden="true">{indicator.symbol}</span>
								<span>{stage.tag}</span>
							</span>
						</div>
						<div
							style={{
								fontFamily: EXPLORER_FONTS.mono,
								fontSize: "11.5px",
								color: "#525866",
								marginTop: "2px",
							}}
						>
							{stage.key} · {stage.persona} · {stage.tier}
						</div>
					</div>
				</div>

				{/* Prev / Next Stage Navigation */}
				<div style={{ display: "flex", alignItems: "center", gap: "6px" }}>
					<button
						type="button"
						data-testid="btn-prev-stage"
						onClick={onPrevStage}
						aria-label="Previous stage"
						style={{
							all: "unset",
							cursor: "pointer",
							fontFamily: EXPLORER_FONTS.mono,
							fontSize: "12px",
							padding: "5px 10px",
							border: "1px solid #E3E5E8",
							borderRadius: "6px",
							color: "#16181D",
							background: "#FFFFFF",
						}}
					>
						←
					</button>
					<button
						type="button"
						data-testid="btn-next-stage"
						onClick={onNextStage}
						aria-label="Next stage"
						style={{
							all: "unset",
							cursor: "pointer",
							fontFamily: EXPLORER_FONTS.mono,
							fontSize: "12px",
							padding: "5px 10px",
							border: "1px solid #E3E5E8",
							borderRadius: "6px",
							color: "#16181D",
							background: "#FFFFFF",
						}}
					>
						→
					</button>
				</div>
			</div>

			<div
				style={{
					fontSize: "13.5px",
					color: "#3B414C",
					lineHeight: 1.5,
					maxWidth: "76ch",
				}}
			>
				{stage.desc}
			</div>
		</div>
	);
}
