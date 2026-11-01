/**
 * Anti-Pattern Counterfactual Inspector Drawer (`CounterfactualTrapDrawer.tsx`)
 * Simulates the 5 Fatal Agentic Traps and Anti-Patterns AP-01 through AP-15 (VIZ-GATE-02) (`docs/06` §4.2).
 */

import { useState } from "react";
import type {
	GateCardPluginProps,
	WorkbenchGateCardPlugin,
} from "../../plugins/plugin-types";

export const VIZ_GATE_02_ID = "VIZ-GATE-02";

export interface FatalTrapDefinition {
	id: string;
	trapNumber: number;
	ruleCode: string;
	title: string;
	targetEntity: string;
	naiveAction: string;
	counterfactualHarm: string;
	openDvResolution: string;
}

export const FATAL_AGENTIC_TRAPS: readonly FatalTrapDefinition[] = [
	{
		id: "AP-01",
		trapNumber: 1,
		ruleCode: "AP-01 / DV-COL-001",
		title: "Surrogate Sequence as Hub Key",
		targetEntity: "investran.src_lp_investors.row_seq_id",
		naiveAction:
			"LLM selects auto-incrementing integer row_seq_id (10001, 10002) as Business Key for hub_limited_partner.",
		counterfactualHarm:
			"Surrogate keys have zero cross-system meaning. Collides with row 10001 from DealCloud; breaks hash join idempotency.",
		openDvResolution:
			"Algorithm A.2 identifies monotonic step Delta=+1 in Quadrant 2. Explicitly tombstones row_seq_id to DROPPED sink.",
	},
	{
		id: "AP-11",
		trapNumber: 2,
		ruleCode: "AP-11 / DV-STR-010",
		title: "Phantom Hub from Unallocated Concept",
		targetEntity: "urn:ontology:entity:custodian_bank",
		naiveAction:
			"LLM hallucinates hub_custodian_bank DDL to satisfy top-down ontology, despite 0 source tables in Graph B.",
		counterfactualHarm:
			"Phantom empty tables bloat metadata, cause orphan foreign keys, and fail deterministic compilation.",
		openDvResolution:
			"Zero-Hallucination Guard checks bipartite coverage (4/5 = 80%). Refuses phantom table generation; marks concept UNALLOCATED.",
	},
	{
		id: "AP-02",
		trapNumber: 3,
		ruleCode: "AP-02 / KEY-01",
		title: "Silent Single-Namespace Hub Collision",
		targetEntity: "legacy_gp.src_legacy_lp_roster.lp_num",
		naiveAction:
			"LLM hashes legacy lp_num='LP-101' into hub_limited_partner without business key collision code (BKCC).",
		counterfactualHarm:
			"$500M CalPERS account collides with €25M Bavarian Pension account on digest 5D15..0AE4; wire instructions leak!",
		openDvResolution:
			"Gate 1 enforces Tier-3 prefix bkcc_prefix='LEGACY_GP_ADMIN'. Hashes Bavarian Pension to 6B7C..D8B9; links via sal_limited_partner.",
	},
	{
		id: "AP-04",
		trapNumber: 4,
		ruleCode: "AP-04 / DV-STR-003",
		title: "Nullable FK in Driving Link",
		targetEntity: "investran.src_lp_commitments_and_calls.co_invest_portco_lei",
		naiveAction:
			"LLM includes optional co_invest_portco_lei (71.4% NULL) directly in mandatory driving lnk_lp_commitment.",
		counterfactualHarm:
			"NULL foreign keys in Data Vault links violate 1NF grain, causing explosion of ghost records and broken join cardinality.",
		openDvResolution:
			"Algorithm A.4 splits optional relationship into lnk_lp_commitment_co_invest with filter WHERE co_invest_portco_lei IS NOT NULL.",
	},
	{
		id: "AP-07",
		trapNumber: 5,
		ruleCode: "AP-07 / ALG-A1",
		title: "High-Frequency Micro-Satellite Bloat",
		targetEntity: "sat_lp_commitment_fx_rates",
		naiveAction:
			"LLM creates separate high-frequency satellite for 3 FX exchange rate columns with only 1 update per day.",
		counterfactualHarm:
			"Excessive satellite fragmentation inflates storage, multiplies staging ELT pipelines, and slows PIT joins.",
		openDvResolution:
			"Gate 4 YAGNI Cost Guard computes write amplification W_amp = 4.20 MB/d < 50.0 MB/d threshold; automatically merges into parent satellite.",
	},
];

export interface CounterfactualTrapDrawerProps {
	isOpen?: boolean;
	onClose?: () => void;
}

export function CounterfactualTrapDrawer({
	isOpen = true,
	onClose,
}: CounterfactualTrapDrawerProps) {
	const [activeTrapId, setActiveTrapId] = useState<string>("AP-01");

	if (!isOpen) return null;

	const defaultTrap = FATAL_AGENTIC_TRAPS[0] as FatalTrapDefinition;
	const activeTrap =
		FATAL_AGENTIC_TRAPS.find((t) => t.id === activeTrapId) ?? defaultTrap;

	return (
		<section
			aria-label="5 Fatal Agentic Traps & Anti-Pattern Counterfactual Simulator"
			data-testid="counterfactual-trap-drawer"
			data-gate-id={VIZ_GATE_02_ID}
			style={{
				backgroundColor: "var(--dv-surface-1)",
				border: "1.5px solid var(--dv-gate-autofix)",
				borderRadius: "0.5rem",
				padding: "1rem",
			}}
		>
			<div
				style={{
					display: "flex",
					justifyContent: "space-between",
					alignItems: "center",
					marginBottom: "0.75rem",
				}}
			>
				<div>
					<div style={{ display: "flex", alignItems: "center", gap: "0.5rem" }}>
						<span
							style={{
								color: "var(--dv-gate-autofix)",
								fontWeight: 700,
								fontSize: "0.75rem",
							}}
						>
							◆ [VIZ-GATE-02]
						</span>
						<h3
							style={{
								margin: 0,
								fontSize: "0.875rem",
								fontWeight: 600,
								color: "var(--dv-text-primary)",
							}}
						>
							The 5 Fatal Agentic Traps: Anti-Pattern Counterfactual Simulator
						</h3>
					</div>
					<span
						style={{ fontSize: "0.75rem", color: "var(--dv-text-secondary)" }}
					>
						Comparing unconstrained LLM failure modes vs. Open-DV deterministic
						guardrails
					</span>
				</div>

				{onClose && (
					<button
						type="button"
						onClick={onClose}
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
						Close Simulator
					</button>
				)}
			</div>

			{/* Trap Selector Buttons */}
			<div
				style={{
					display: "flex",
					flexWrap: "wrap",
					gap: "0.5rem",
					marginBottom: "1rem",
				}}
			>
				{FATAL_AGENTIC_TRAPS.map((trap) => {
					const isSelected = trap.id === activeTrapId;
					return (
						<button
							key={trap.id}
							type="button"
							data-testid={`trap-btn-${trap.id}`}
							onClick={() => setActiveTrapId(trap.id)}
							style={{
								backgroundColor: isSelected
									? "var(--dv-surface-3)"
									: "var(--dv-surface-2)",
								color: "var(--dv-text-primary)",
								border: isSelected
									? "2px solid var(--dv-gate-autofix)"
									: "1px solid var(--dv-border-subtle)",
								borderRadius: "0.25rem",
								padding: "0.35rem 0.6rem",
								fontSize: "0.75rem",
								fontWeight: isSelected ? 700 : 500,
								cursor: "pointer",
							}}
						>
							Trap {trap.trapNumber}: {trap.title}
						</button>
					);
				})}
			</div>

			{/* Active Trap Counterfactual Card */}
			<div
				data-testid={`trap-card-${activeTrap.id}`}
				style={{
					backgroundColor: "var(--dv-surface-2)",
					border: "1px solid var(--dv-border-strong)",
					borderRadius: "0.375rem",
					padding: "0.75rem 1rem",
				}}
			>
				<div
					style={{
						display: "flex",
						justifyContent: "space-between",
						alignItems: "center",
						marginBottom: "0.5rem",
					}}
				>
					<strong
						style={{ fontSize: "0.875rem", color: "var(--dv-text-primary)" }}
					>
						Trap {activeTrap.trapNumber}: {activeTrap.title} (
						{activeTrap.ruleCode})
					</strong>
					<span
						style={{
							fontFamily: "ui-monospace, monospace",
							fontSize: "0.7rem",
							backgroundColor: "var(--dv-surface-1)",
							padding: "0.15rem 0.4rem",
							borderRadius: "0.25rem",
							border: "1px solid var(--dv-border-subtle)",
						}}
					>
						Target: {activeTrap.targetEntity}
					</span>
				</div>

				<div
					style={{
						display: "grid",
						gridTemplateColumns: "1fr 1fr",
						gap: "1rem",
						marginTop: "0.75rem",
					}}
				>
					{/* Unconstrained LLM Failure */}
					<div
						style={{
							backgroundColor: "var(--dv-surface-1)",
							border: "1.5px solid var(--dv-gate-reject)",
							borderRadius: "0.375rem",
							padding: "0.75rem",
						}}
					>
						<div
							style={{
								fontSize: "0.75rem",
								fontWeight: 700,
								color: "var(--dv-gate-reject)",
								marginBottom: "0.4rem",
							}}
						>
							■ Unconstrained LLM Anti-Pattern Action
						</div>
						<p
							style={{
								margin: "0 0 0.5rem",
								fontSize: "0.75rem",
								color: "var(--dv-text-secondary)",
								lineHeight: 1.4,
							}}
						>
							{activeTrap.naiveAction}
						</p>
						<div
							style={{
								fontSize: "0.7rem",
								color: "var(--dv-gate-reject)",
								borderTop: "1px solid var(--dv-border-subtle)",
								paddingTop: "0.4rem",
							}}
						>
							<strong>Fatal Harm: </strong>
							{activeTrap.counterfactualHarm}
						</div>
					</div>

					{/* Open-DV Guard & Resolution */}
					<div
						style={{
							backgroundColor: "var(--dv-surface-1)",
							border: "1.5px solid var(--dv-gate-pass)",
							borderRadius: "0.375rem",
							padding: "0.75rem",
						}}
					>
						<div
							style={{
								fontSize: "0.75rem",
								fontWeight: 700,
								color: "var(--dv-gate-pass)",
								marginBottom: "0.4rem",
							}}
						>
							● Open-DV Deterministic Resolution
						</div>
						<p
							style={{
								margin: 0,
								fontSize: "0.75rem",
								color: "var(--dv-text-secondary)",
								lineHeight: 1.4,
							}}
						>
							{activeTrap.openDvResolution}
						</p>
					</div>
				</div>
			</div>
		</section>
	);
}

export const VIZ_GATE_02_PLUGIN: WorkbenchGateCardPlugin = {
	gateId: VIZ_GATE_02_ID,
	title: "The 5 Fatal Agentic Traps Counterfactual Simulator",
	ruleIds: [
		"AP-01",
		"AP-02",
		"AP-04",
		"AP-07",
		"AP-11",
		"DV-COL-001",
		"DV-STR-010",
	],
	component: (props: GateCardPluginProps) => (
		<CounterfactualTrapDrawer
			isOpen={props.isExpanded}
			onClose={props.onToggleExpand}
		/>
	),
};
