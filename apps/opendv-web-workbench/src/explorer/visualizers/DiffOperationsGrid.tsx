/**
 * Detailed Operations Grid for Ontology Diff Visualizer (User Directive R9 Modularity).
 */

import { EXPLORER_COLORS, EXPLORER_FONTS } from "../design-tokens";

export interface DiffOperationsGridProps {
	addedConcepts: string[];
	addedRelationships: string[];
	addedEnumerations: string[];
	refinedConcepts: string[];
}

export function DiffOperationsGrid({
	addedConcepts,
	addedRelationships,
	addedEnumerations,
	refinedConcepts,
}: DiffOperationsGridProps) {
	return (
		<div style={{ display: "flex", flexDirection: "column", gap: "12px" }}>
			{/* Added Concepts */}
			{addedConcepts.length > 0 && (
				<div
					data-testid="added-concepts-section"
					style={{
						padding: "10px 12px",
						borderRadius: "6px",
						border: `1px solid ${EXPLORER_COLORS.borderLine}`,
						background: EXPLORER_COLORS.bgPage,
					}}
				>
					<div
						style={{
							fontSize: "11.5px",
							fontWeight: 600,
							textTransform: "uppercase",
							letterSpacing: "0.06em",
							color: "#15803D",
							marginBottom: "6px",
						}}
					>
						Added Concepts ({addedConcepts.length})
					</div>
					<ul
						style={{
							margin: "0 0 0 16px",
							padding: 0,
							fontSize: "12px",
							fontFamily: EXPLORER_FONTS.mono,
							color: EXPLORER_COLORS.inkPrimary,
							display: "flex",
							flexDirection: "column",
							gap: "4px",
						}}
					>
						{addedConcepts.map((c) => (
							<li key={c}>
								<span style={{ color: "#15803D", fontWeight: 600 }}>+ </span>
								{c}
							</li>
						))}
					</ul>
				</div>
			)}

			{/* Added Relationships */}
			{addedRelationships.length > 0 && (
				<div
					data-testid="added-relationships-section"
					style={{
						padding: "10px 12px",
						borderRadius: "6px",
						border: `1px solid ${EXPLORER_COLORS.borderLine}`,
						background: EXPLORER_COLORS.bgPage,
					}}
				>
					<div
						style={{
							fontSize: "11.5px",
							fontWeight: 600,
							textTransform: "uppercase",
							letterSpacing: "0.06em",
							color: "#B45309",
							marginBottom: "6px",
						}}
					>
						Added Relationships ({addedRelationships.length})
					</div>
					<ul
						style={{
							margin: "0 0 0 16px",
							padding: 0,
							fontSize: "12px",
							fontFamily: EXPLORER_FONTS.mono,
							color: EXPLORER_COLORS.inkPrimary,
							display: "flex",
							flexDirection: "column",
							gap: "4px",
						}}
					>
						{addedRelationships.map((r) => (
							<li key={r}>
								<span style={{ color: "#B45309", fontWeight: 600 }}>+ </span>
								{r}
							</li>
						))}
					</ul>
				</div>
			)}

			{/* Added Enumerations */}
			{addedEnumerations.length > 0 && (
				<div
					data-testid="added-enumerations-section"
					style={{
						padding: "10px 12px",
						borderRadius: "6px",
						border: `1px solid ${EXPLORER_COLORS.borderLine}`,
						background: EXPLORER_COLORS.bgPage,
					}}
				>
					<div
						style={{
							fontSize: "11.5px",
							fontWeight: 600,
							textTransform: "uppercase",
							letterSpacing: "0.06em",
							color: "#4B5563",
							marginBottom: "6px",
						}}
					>
						Added Enumerations ({addedEnumerations.length})
					</div>
					<ul
						style={{
							margin: "0 0 0 16px",
							padding: 0,
							fontSize: "12px",
							fontFamily: EXPLORER_FONTS.mono,
							color: EXPLORER_COLORS.inkPrimary,
							display: "flex",
							flexDirection: "column",
							gap: "4px",
						}}
					>
						{addedEnumerations.map((e) => (
							<li key={e}>
								<span style={{ color: "#4B5563", fontWeight: 600 }}>+ </span>
								{e}
							</li>
						))}
					</ul>
				</div>
			)}

			{/* Refined Concepts */}
			{refinedConcepts.length > 0 && (
				<div
					data-testid="refined-concepts-section"
					style={{
						padding: "10px 12px",
						borderRadius: "6px",
						border: `1px solid ${EXPLORER_COLORS.borderLine}`,
						background: EXPLORER_COLORS.bgPage,
					}}
				>
					<div
						style={{
							fontSize: "11.5px",
							fontWeight: 600,
							textTransform: "uppercase",
							letterSpacing: "0.06em",
							color: "#1D4ED8",
							marginBottom: "6px",
						}}
					>
						Refined Concepts ({refinedConcepts.length})
					</div>
					<ul
						style={{
							margin: "0 0 0 16px",
							padding: 0,
							fontSize: "12px",
							fontFamily: EXPLORER_FONTS.mono,
							color: EXPLORER_COLORS.inkPrimary,
							display: "flex",
							flexDirection: "column",
							gap: "4px",
						}}
					>
						{refinedConcepts.map((rc) => (
							<li key={rc}>
								<span style={{ color: "#1D4ED8", fontWeight: 600 }}>~ </span>
								{rc}
							</li>
						))}
					</ul>
				</div>
			)}
		</div>
	);
}
