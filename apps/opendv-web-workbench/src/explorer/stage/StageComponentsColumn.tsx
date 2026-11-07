import type { Stage } from "@opendv/protocol-ts";
import {
	EXPLORER_FONTS,
	EXPLORER_GLYPHS,
	getGlyphCssStyle,
} from "../design-tokens";
import type { ExplorerSelectionType } from "../types";

export interface StageComponentsColumnProps {
	stage: Stage;
	selType: ExplorerSelectionType;
	selId: string | number | null;
	onSelectComponent: (compIdx: number) => void;
}

export function StageComponentsColumn({
	stage,
	selType,
	selId,
	onSelectComponent,
}: StageComponentsColumnProps) {
	return (
		<section
			aria-label="Stage Components"
			onClick={(ev) => ev.stopPropagation()}
			style={{
				width: "260px",
				flexShrink: 0,
				display: "flex",
				flexDirection: "column",
				gap: "8px",
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
					style={{
						fontSize: "11px",
						fontWeight: 600,
						textTransform: "uppercase",
						letterSpacing: "0.06em",
						color: "#6B7280",
					}}
				>
					Components
				</span>
				<span
					style={{
						fontFamily: EXPLORER_FONTS.mono,
						fontSize: "11px",
						color: "#6B7280",
					}}
				>
					agents &amp; modules
				</span>
			</div>

			{stage.comps.map((comp, idx) => {
				const isSelected = selType === "component" && selId === idx;
				return (
					<div
						key={`${comp[0]}-${idx}`}
						data-testid={`stage-component-${comp[0]}`}
						onClick={() => onSelectComponent(idx)}
						onKeyDown={(ev) => {
							if (ev.key === "Enter" || ev.key === " ") {
								ev.preventDefault();
								onSelectComponent(idx);
							} else if (ev.key === "ArrowDown") {
								ev.preventDefault();
								const items = Array.from(
									ev.currentTarget.parentElement?.querySelectorAll<HTMLElement>(
										'[data-testid^="stage-component-"]',
									) ?? [],
								);
								const currentIdx = items.indexOf(ev.currentTarget);
								if (currentIdx >= 0 && currentIdx < items.length - 1) {
									items[currentIdx + 1]?.focus();
								}
							} else if (ev.key === "ArrowUp") {
								ev.preventDefault();
								const items = Array.from(
									ev.currentTarget.parentElement?.querySelectorAll<HTMLElement>(
										'[data-testid^="stage-component-"]',
									) ?? [],
								);
								const currentIdx = items.indexOf(ev.currentTarget);
								if (currentIdx > 0) {
									items[currentIdx - 1]?.focus();
								}
							}
						}}
						tabIndex={0}
						role="button"
						aria-pressed={isSelected}
						aria-label={`Component ${comp[0]}, type ${comp[1]}`}
						style={{
							background: "#FFFFFF",
							border: `1.5px solid ${isSelected ? "#1D4ED8" : "#E3E5E8"}`,
							borderRadius: "6px",
							padding: "9px 11px",
							cursor: "pointer",
							display: "flex",
							alignItems: "center",
							justifyContent: "space-between",
							gap: "8px",
						}}
					>
						<div
							style={{
								display: "flex",
								alignItems: "center",
								gap: "8px",
								minWidth: 0,
							}}
						>
							<span
								style={getGlyphCssStyle(EXPLORER_GLYPHS.component, 10)}
							/>
							<span
								style={{
									fontFamily: EXPLORER_FONTS.mono,
									fontSize: "12px",
									fontWeight: 500,
									color: "#16181D",
									overflow: "hidden",
									textOverflow: "ellipsis",
									whiteSpace: "nowrap",
								}}
							>
								{comp[0]}
							</span>
						</div>
						<span
							style={{
								fontFamily: EXPLORER_FONTS.mono,
								fontSize: "10.5px",
								color: "#6B7280",
								flexShrink: 0,
							}}
						>
							{comp[1]}
						</span>
					</div>
				);
			})}
		</section>
	);
}
