import type { CSSProperties, ReactNode } from "react";
import { EXPLORER_COLORS, EXPLORER_FONTS } from "../design-tokens";

export interface StewardHoldCandidate {
	id: string;
	label: string;
	score?: number | undefined;
}

const LBL: CSSProperties = {
	fontSize: "11px",
	fontWeight: 600,
	color: EXPLORER_COLORS.inkLabel,
	textTransform: "uppercase",
};

const INP: CSSProperties = {
	fontFamily: EXPLORER_FONTS.sans,
	fontSize: "12px",
	padding: "8px 10px",
	borderRadius: "6px",
	border: `1px solid ${EXPLORER_COLORS.borderLine}`,
	color: EXPLORER_COLORS.inkPrimary,
};

export interface StewardDecisionFieldsProps {
	action: "APPROVE" | "REJECT" | "ANSWER";
	setAction: (action: "APPROVE" | "REJECT" | "ANSWER") => void;
	candidates?: readonly StewardHoldCandidate[] | undefined;
	selectedCandidate: string;
	setSelectedCandidate: (id: string) => void;
	rationale: string;
	setRationale: (r: string) => void;
	isSubmitting: boolean;
	children?: ReactNode | undefined;
}

export function StewardDecisionFields({
	action,
	setAction,
	candidates,
	selectedCandidate,
	setSelectedCandidate,
	rationale,
	setRationale,
	isSubmitting,
	children,
}: StewardDecisionFieldsProps) {
	return (
		<>
			{/* Action Buttons */}
			<div style={{ display: "flex", flexDirection: "column", gap: "4px" }}>
				<span style={LBL}>Steward Action</span>
				<div style={{ display: "flex", gap: "8px" }}>
					{(["APPROVE", "REJECT", "ANSWER"] as const).map((act) => (
						<button
							key={act}
							type="button"
							data-testid={`hold-action-${act.toLowerCase()}`}
							onClick={() => setAction(act)}
							disabled={isSubmitting}
							style={{
								flex: 1,
								padding: "8px",
								borderRadius: "6px",
								fontSize: "12px",
								fontWeight: 600,
								cursor: isSubmitting ? "not-allowed" : "pointer",
								border:
									action === act
										? "1px solid #16181D"
										: `1px solid ${EXPLORER_COLORS.borderLine}`,
								background:
									action === act ? "#16181D" : EXPLORER_COLORS.bgPage,
								color:
									action === act ? "#FFFFFF" : EXPLORER_COLORS.inkPrimary,
							}}
						>
							{act}
						</button>
					))}
				</div>
			</div>

			{/* Candidate Selection Dropdown */}
			{candidates && candidates.length > 0 && action === "APPROVE" && (
				<div
					style={{ display: "flex", flexDirection: "column", gap: "4px" }}
				>
					<label htmlFor="hold-candidate-select" style={LBL}>
						Candidate Entity
					</label>
					<select
						id="hold-candidate-select"
						data-testid="select-hold-candidate"
						value={selectedCandidate}
						onChange={(e) => setSelectedCandidate(e.target.value)}
						disabled={isSubmitting}
						style={{ ...INP, fontFamily: EXPLORER_FONTS.mono }}
					>
						{candidates.map((c) => (
							<option key={c.id} value={c.id}>
								{c.label} ({c.id})
							</option>
						))}
					</select>
				</div>
			)}

			{/* Ontology Delta Checklist / Custom Slot */}
			{children}

			{/* Justification Notes */}
			<div style={{ display: "flex", flexDirection: "column", gap: "4px" }}>
				<label htmlFor="steward-rationale" style={LBL}>
					Steward Justification Notes
				</label>
				<textarea
					id="steward-rationale"
					data-testid="input-steward-rationale"
					rows={2}
					value={rationale}
					onChange={(e) => setRationale(e.target.value)}
					placeholder="Enter steward decision rationale..."
					disabled={isSubmitting}
					style={INP}
				/>
			</div>
		</>
	);
}
