/**
 * Step 6 Dynamic Gate 0–6 Scorecard Badges (`UAT-12`, `DESIGN.md`)
 * (apps/opendv-web-workbench/src/ontology/wizard/Step6GateBadges.tsx)
 */

import type React from "react";
import { CLAUDE_TOKENS, EXPLORER_FONTS } from "../../explorer/design-tokens";

export interface Step6GateBadgesProps {
	gateScorecard?: Record<string, unknown> | null | undefined;
}

type GateTone = "success" | "error" | "warning" | "neutral";

interface GateItem {
	index: number;
	label: string;
	statusText: string;
	tone: GateTone;
}

const GATE_KEY_PAIRS: ReadonlyArray<{
	index: number;
	primary: string;
	fallback: string;
}> = [
	{ index: 0, primary: "gate_0_profile_drift", fallback: "gate_0" },
	{ index: 1, primary: "gate_1_contract_grain", fallback: "gate_1" },
	{ index: 2, primary: "gate_2_sqlglot_ast", fallback: "gate_2" },
	{ index: 3, primary: "gate_3_networkx_dag", fallback: "gate_3" },
	{ index: 4, primary: "gate_4_yagni_cost_guard", fallback: "gate_4" },
	{ index: 5, primary: "gate_5_duckdb_sandbox", fallback: "gate_5" },
];

const PASS_VALUES = new Set([
	"PASS",
	"AUTO-FIX",
	"AUTO_REPAIRED",
	"OK",
	"TRUE",
]);
const FAIL_VALUES = new Set([
	"FAIL",
	"FAILED",
	"REJECT-WITH-TRACE",
	"ERROR",
	"FALSE",
]);
const WARN_VALUES = new Set(["HOLD", "PAUSED_HOLD", "WARN", "WARNING"]);

function classifyGateValue(raw: unknown): {
	statusText: string;
	tone: GateTone;
} {
	if (raw === null || raw === undefined) {
		return { statusText: "PENDING", tone: "neutral" };
	}
	if (raw === true) return { statusText: "PASS", tone: "success" };
	if (raw === false) return { statusText: "FAIL", tone: "error" };
	const text = String(raw).trim();
	if (!text) return { statusText: "PENDING", tone: "neutral" };
	const upper = text.toUpperCase();
	if (PASS_VALUES.has(upper)) return { statusText: upper, tone: "success" };
	if (FAIL_VALUES.has(upper)) return { statusText: upper, tone: "error" };
	if (WARN_VALUES.has(upper)) return { statusText: upper, tone: "warning" };
	return { statusText: upper, tone: "neutral" };
}

function deriveGate6(scorecard?: Record<string, unknown> | null): GateItem {
	if (!scorecard || typeof scorecard !== "object") {
		return {
			index: 6,
			label: "Gate 6",
			statusText: "PENDING",
			tone: "neutral",
		};
	}
	const summary = scorecard.sandbox_summary as
		| Record<string, unknown>
		| undefined;
	const passedCount = summary?.dynamic_checks_passed ?? scorecard.dv_dyn_passed;
	const totalCount = summary?.dynamic_checks_total ?? scorecard.dv_dyn_total;
	if (
		typeof passedCount === "number" &&
		typeof totalCount === "number" &&
		Number.isFinite(passedCount) &&
		Number.isFinite(totalCount) &&
		totalCount > 0
	) {
		const summaryPassed =
			summary?.passed !== false && scorecard.sandbox_passed !== false;
		const allOk = passedCount === totalCount && summaryPassed;
		return {
			index: 6,
			label: "Gate 6",
			statusText: `${passedCount}/${totalCount} DV-DYN`,
			tone: allOk ? "success" : "error",
		};
	}
	const raw =
		scorecard.gate_6 ?? scorecard.sandbox_passed ?? scorecard.shadow_passed;
	const { statusText, tone } = classifyGateValue(raw);
	return { index: 6, label: "Gate 6", statusText, tone };
}

export function deriveGateBadgeItems(
	scorecard?: Record<string, unknown> | null,
): GateItem[] {
	const items: GateItem[] = GATE_KEY_PAIRS.map(
		({ index, primary, fallback }) => {
			const raw =
				scorecard && typeof scorecard === "object"
					? (scorecard[primary] ?? scorecard[fallback])
					: undefined;
			const { statusText, tone } = classifyGateValue(raw);
			return { index, label: `Gate ${index}`, statusText, tone };
		},
	);
	items.push(deriveGate6(scorecard));
	return items;
}

function toneColor(tone: GateTone): string {
	switch (tone) {
		case "success":
			return CLAUDE_TOKENS.colors.success;
		case "error":
			return CLAUDE_TOKENS.colors.error;
		case "warning":
			return CLAUDE_TOKENS.colors.warning;
		default:
			return CLAUDE_TOKENS.colors.onDarkSoft;
	}
}

export const Step6GateBadges: React.FC<Step6GateBadgesProps> = ({
	gateScorecard,
}) => {
	const items = deriveGateBadgeItems(gateScorecard);

	return (
		<div
			data-testid="step6-gate-badges"
			style={{
				display: "flex",
				flexWrap: "wrap",
				gap: "6px",
				alignItems: "center",
			}}
		>
			{items.map((item) => {
				const fg = toneColor(item.tone);
				return (
					<span
						key={item.index}
						data-testid={`step6-gate-badge-${item.index}`}
						style={{
							display: "inline-flex",
							alignItems: "center",
							gap: "5px",
							padding: "3px 8px",
							borderRadius: "999px",
							backgroundColor: CLAUDE_TOKENS.colors.surfaceDarkElevated,
							border: `1px solid ${item.tone === "neutral" ? "#33312c" : fg}`,
							color: fg,
							fontFamily: EXPLORER_FONTS.mono,
							fontSize: "11px",
							fontWeight: 500,
						}}
					>
						<span aria-hidden="true">●</span>
						<span>
							{item.label} · {item.statusText}
						</span>
					</span>
				);
			})}
		</div>
	);
};
