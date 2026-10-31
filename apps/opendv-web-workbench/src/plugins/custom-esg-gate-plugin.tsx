/**
 * Reference Custom Domain Gate Card Plugin (`custom-esg-gate-plugin.tsx`) (`docs/10` §3.3).
 *
 * Demonstrates third-party domain plugin registration (`VIZ-GATE-ESG-ILPA`) for Private Equity
 * ILPA ESG disclosure and additive companion Satellite drift (`ALG-A9A`, `EVO-01`, `ESG-ILPA-001`)
 * without modifying any core Workbench Shell files.
 */

import { VERDICT_VISUAL_GRAMMAR } from "../theme/theme-registry";
import { WorkbenchPluginRegistry } from "./plugin-registry";
import type {
	GateCardPluginProps,
	WorkbenchGateCardPlugin,
} from "./plugin-types";

export const CUSTOM_ESG_GATE_ID = "VIZ-GATE-ESG-ILPA";

export const CUSTOM_ESG_RULE_IDS: readonly string[] = [
	"ALG-A9A",
	"EVO-01",
	"ESG-ILPA-001",
];

export function CustomEsgGateCard({
	event,
	isExpanded,
	onToggleExpand,
}: GateCardPluginProps) {
	const verdictMeta =
		VERDICT_VISUAL_GRAMMAR[event.status] ?? VERDICT_VISUAL_GRAMMAR.PASS;
	const pills = event.layer2_visual_payload.semantic_hash_pills ?? [];
	const rules = event.rule_ids_evaluated ?? [];

	return (
		<article
			aria-label="ILPA ESG Rating & Drift Companion Gate Card"
			data-testid="custom-esg-gate-card"
			data-gate-id={CUSTOM_ESG_GATE_ID}
			style={{
				backgroundColor: "var(--dv-surface-1)",
				border: `1.5px solid var(${verdictMeta.cssColorVar})`,
				borderRadius: "0.5rem",
				padding: "0.75rem 1rem",
				color: "var(--dv-text-primary)",
			}}
		>
			<div
				style={{
					display: "flex",
					justifyContent: "space-between",
					alignItems: "center",
					gap: "0.75rem",
				}}
			>
				<div>
					<span
						style={{
							fontWeight: 700,
							fontSize: "0.75rem",
							color: `var(${verdictMeta.cssColorVar})`,
							marginRight: "0.5rem",
						}}
					>
						{verdictMeta.shapeSymbol} {verdictMeta.prefixBadge}
					</span>
					<strong style={{ fontSize: "0.875rem" }}>
						ILPA ESG Rating &amp; Drift Companion Gate ({CUSTOM_ESG_GATE_ID})
					</strong>
				</div>
				<button
					type="button"
					aria-expanded={isExpanded}
					onClick={onToggleExpand}
					style={{
						backgroundColor: "var(--dv-surface-2)",
						color: "var(--dv-text-primary)",
						border: "1px solid var(--dv-border-strong)",
						borderRadius: "0.25rem",
						padding: "0.25rem 0.5rem",
						fontSize: "0.75rem",
						cursor: "pointer",
					}}
				>
					{isExpanded ? "Collapse ESG Audit" : "Expand ESG Audit"}
				</button>
			</div>

			<p
				style={{
					margin: "0.5rem 0 0",
					fontSize: "0.8125rem",
					color: "var(--dv-text-secondary)",
				}}
			>
				{event.layer1_story_banner.headline}
			</p>

			{isExpanded && (
				<div
					data-testid="custom-esg-gate-details"
					style={{
						marginTop: "0.75rem",
						paddingTop: "0.75rem",
						borderTop: "1px solid var(--dv-border-subtle)",
						fontSize: "0.75rem",
					}}
				>
					<div style={{ marginBottom: "0.5rem" }}>
						<strong>Evaluated Rules: </strong>
						<span>{rules.length > 0 ? rules.join(", ") : "ESG-ILPA-001"}</span>
					</div>
					<div>
						<strong>Semantic Hash Pills: </strong>
						{pills.map((pill) => (
							<code
								key={`${pill.full_sha256_hex}-${pill.business_key}`}
								title={pill.full_sha256_hex}
								style={{
									display: "inline-block",
									backgroundColor: "var(--dv-surface-2)",
									border: "1px solid var(--dv-border-strong)",
									borderRadius: "0.25rem",
									padding: "0.125rem 0.375rem",
									marginRight: "0.375rem",
								}}
							>
								{pill.pill_label}
							</code>
						))}
					</div>
				</div>
			)}
		</article>
	);
}

export const CUSTOM_ESG_GATE_PLUGIN: WorkbenchGateCardPlugin = {
	gateId: CUSTOM_ESG_GATE_ID,
	title: "ILPA ESG Rating & Drift Companion Gate",
	ruleIds: [...CUSTOM_ESG_RULE_IDS],
	component: CustomEsgGateCard,
};

export function registerCustomEsgGatePlugin(
	registry:
		| WorkbenchPluginRegistry
		| typeof WorkbenchPluginRegistry = WorkbenchPluginRegistry,
): WorkbenchGateCardPlugin {
	return registry.registerGateCard(CUSTOM_ESG_GATE_PLUGIN);
}
