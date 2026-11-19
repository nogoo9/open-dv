/**
 * Shared Budget Steward Hold Card (`D21`)
 * Rendered identically on Step 4 and Step 6 when a budget stop occurs.
 * (apps/opendv-web-workbench/src/ontology/wizard/BudgetHoldCard.tsx)
 */

import type React from "react";
import { CLAUDE_TOKENS } from "../../explorer/design-tokens";
import type { BudgetHoldInfo } from "../store/types";

export interface BudgetHoldCardProps {
	budgetHold?: BudgetHoldInfo | null | undefined;
	onRaiseBudgetAndResume?: (() => Promise<void> | void) | undefined;
	onAbortBudgetHold?: (() => Promise<void> | void) | undefined;
}

export const BudgetHoldCard: React.FC<BudgetHoldCardProps> = ({
	budgetHold,
	onRaiseBudgetAndResume,
	onAbortBudgetHold,
}) => {
	if (!budgetHold) return null;
	return (
		<div
			data-testid="budget-steward-hold-card"
			className="claude-card"
			style={{
				padding: "14px 16px",
				borderLeft: `4px solid ${CLAUDE_TOKENS.colors.warning}`,
				background: "#fdf6e7",
			}}
		>
			<div
				style={{
					display: "flex",
					justifyContent: "space-between",
					alignItems: "center",
					flexWrap: "wrap",
					gap: "10px",
				}}
			>
				<div>
					<strong style={{ fontSize: "13.5px" }}>
						Budget Steward Hold · {budgetHold.reason}
					</strong>
					<div style={{ fontSize: "12px", color: CLAUDE_TOKENS.colors.body }}>
						Job {budgetHold.job_id} paused ({budgetHold.total_tokens ?? 0} tok ·
						${Number(budgetHold.cost_usd ?? 0).toFixed(4)}).
					</div>
				</div>
				<div style={{ display: "flex", gap: "8px" }}>
					<button
						type="button"
						className="claude-btn-primary"
						data-testid="btn-raise-budget-resume"
						onClick={() => void onRaiseBudgetAndResume?.()}
					>
						Raise budget &amp; resume
					</button>
					<button
						type="button"
						className="claude-btn-secondary"
						data-testid="btn-abort-budget-hold"
						onClick={() => void onAbortBudgetHold?.()}
					>
						Abort
					</button>
				</div>
			</div>
		</div>
	);
};
