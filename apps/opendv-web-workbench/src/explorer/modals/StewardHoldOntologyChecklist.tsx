import type { StewardHoldOntologyOp } from "../StewardHoldModal";
import { EXPLORER_COLORS, EXPLORER_FONTS } from "../design-tokens";

export interface StewardHoldOntologyChecklistProps {
	ontologyOps: readonly StewardHoldOntologyOp[];
	selectedOpIds: Set<string>;
	onToggleOp: (opId: string) => void;
	onSelectAll: () => void;
	onDeselectAll: () => void;
	isSubmitting: boolean;
	action: "APPROVE" | "REJECT" | "ANSWER";
}

export function StewardHoldOntologyChecklist({
	ontologyOps,
	selectedOpIds,
	onToggleOp,
	onSelectAll,
	onDeselectAll,
	isSubmitting,
	action,
}: StewardHoldOntologyChecklistProps) {
	return (
		<div
			data-testid="ontology-delta-checklist"
			style={{
				display: "flex",
				flexDirection: "column",
				gap: "6px",
				padding: "10px",
				borderRadius: "6px",
				background: EXPLORER_COLORS.bgPage,
				border: `1px solid ${EXPLORER_COLORS.borderLine}`,
			}}
		>
			<div
				style={{
					display: "flex",
					justifyContent: "space-between",
					alignItems: "center",
				}}
			>
				<span
					style={{
						fontSize: "11px",
						fontWeight: 600,
						color: EXPLORER_COLORS.inkLabel,
						textTransform: "uppercase",
					}}
				>
					Ontology Operations ({selectedOpIds.size}/{ontologyOps.length})
				</span>
				<div style={{ display: "flex", gap: "6px" }}>
					<button
						type="button"
						data-testid="btn-select-all-ops"
						onClick={onSelectAll}
						disabled={isSubmitting}
						style={{
							all: "unset",
							cursor: "pointer",
							fontSize: "11px",
							color: "#1D4ED8",
						}}
					>
						Select all
					</button>
					<span style={{ color: EXPLORER_COLORS.borderLine }}>|</span>
					<button
						type="button"
						data-testid="btn-deselect-all-ops"
						onClick={onDeselectAll}
						disabled={isSubmitting}
						style={{
							all: "unset",
							cursor: "pointer",
							fontSize: "11px",
							color: EXPLORER_COLORS.inkLabel,
						}}
					>
						Deselect all
					</button>
				</div>
			</div>
			<div
				style={{
					display: "flex",
					flexDirection: "column",
					gap: "4px",
					maxHeight: "160px",
					overflowY: "auto",
				}}
			>
				{ontologyOps.map((op) => (
					<label
						key={op.op_id}
						data-testid={`op-checkbox-label-${op.op_id}`}
						style={{
							display: "flex",
							alignItems: "center",
							gap: "6px",
							padding: "4px",
							fontSize: "11.5px",
							cursor: isSubmitting ? "not-allowed" : "pointer",
						}}
					>
						<input
							type="checkbox"
							data-testid={`checkbox-op-${op.op_id}`}
							checked={selectedOpIds.has(op.op_id)}
							onChange={() => onToggleOp(op.op_id)}
							disabled={isSubmitting || action === "REJECT"}
						/>
						<span
							style={{
								fontFamily: EXPLORER_FONTS.mono,
								fontWeight: 600,
							}}
						>
							{op.op_id}
						</span>
						<span
							style={{
								fontSize: "10px",
								padding: "1px 5px",
								borderRadius: "3px",
								background: "#EEF2FF",
								color: "#1D4ED8",
							}}
						>
							{op.op_type.replace(/_/g, " ")}
						</span>
						<span style={{ color: EXPLORER_COLORS.inkBody }}>
							{op.concept_name ??
								op.relationship_name ??
								op.concept_uri ??
								op.relationship_uri}
						</span>
					</label>
				))}
			</div>
		</div>
	);
}
