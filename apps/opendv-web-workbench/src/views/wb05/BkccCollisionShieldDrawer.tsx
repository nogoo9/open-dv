/**
 * BKCC Collision Shield Drawer (`BkccCollisionShieldDrawer.tsx`)
 * Visualizes 3-Tier Business Key Authority & BKCC Hash Collision Prevention (VIZ-GATE-04) (`docs/06` §3.7, §4.4).
 */

import { useState } from "react";
import type {
	GateCardPluginProps,
	WorkbenchGateCardPlugin,
} from "../../plugins/plugin-types";

export const VIZ_GATE_04_ID = "VIZ-GATE-04";

export interface BkccCollisionShieldDrawerProps {
	isOpen?: boolean;
	onClose?: () => void;
}

export function BkccCollisionShieldDrawer({
	isOpen = true,
	onClose,
}: BkccCollisionShieldDrawerProps) {
	const [activeTab, setActiveTab] = useState<
		"COLLISION_SHIELD" | "PASSIVE_INTEGRATION"
	>("COLLISION_SHIELD");

	if (!isOpen) return null;

	return (
		<section
			aria-label="3-Tier BKCC Collision Shield Drawer"
			data-testid="bkcc-collision-shield-drawer"
			data-gate-id={VIZ_GATE_04_ID}
			style={{
				backgroundColor: "var(--dv-surface-1)",
				border: "1.5px solid var(--dv-gate-autofix)",
				borderRadius: "0.5rem",
				padding: "1rem",
				display: "flex",
				flexDirection: "column",
				gap: "1rem",
			}}
		>
			{/* Drawer Header */}
			<div
				style={{
					display: "flex",
					justifyContent: "space-between",
					alignItems: "center",
					borderBottom: "1px solid var(--dv-border-subtle)",
					paddingBottom: "0.5rem",
				}}
			>
				<div>
					<div style={{ display: "flex", alignItems: "center", gap: "0.5rem" }}>
						<span
							style={{
								color: "var(--dv-gate-pass)",
								fontWeight: 700,
								fontSize: "0.75rem",
							}}
						>
							● [VIZ-GATE-04 PASS]
						</span>
						<h3
							style={{
								margin: 0,
								fontSize: "0.875rem",
								fontWeight: 700,
								color: "var(--dv-text-primary)",
							}}
						>
							Gate 1 3-Tier Business Key Authority &amp; BKCC Collision Shield
						</h3>
					</div>
					<span
						style={{ fontSize: "0.7rem", color: "var(--dv-text-secondary)" }}
					>
						Algorithm A.5 Length-Prefixed Key Formatting Preventing Multi-Source
						Hash Collisions (KEY-01..05)
					</span>
				</div>

				<div style={{ display: "flex", gap: "0.5rem" }}>
					<button
						type="button"
						onClick={() => setActiveTab("COLLISION_SHIELD")}
						style={{
							backgroundColor:
								activeTab === "COLLISION_SHIELD"
									? "var(--dv-surface-3)"
									: "var(--dv-surface-2)",
							color: "var(--dv-text-primary)",
							border: "1px solid var(--dv-border-strong)",
							borderRadius: "0.25rem",
							padding: "0.25rem 0.5rem",
							fontSize: "0.7rem",
							fontWeight: activeTab === "COLLISION_SHIELD" ? 700 : 500,
							cursor: "pointer",
						}}
					>
						CalPERS vs. Bavarian Shield
					</button>

					<button
						type="button"
						onClick={() => setActiveTab("PASSIVE_INTEGRATION")}
						style={{
							backgroundColor:
								activeTab === "PASSIVE_INTEGRATION"
									? "var(--dv-surface-3)"
									: "var(--dv-surface-2)",
							color: "var(--dv-text-primary)",
							border: "1px solid var(--dv-border-strong)",
							borderRadius: "0.25rem",
							padding: "0.25rem 0.5rem",
							fontSize: "0.7rem",
							fontWeight: activeTab === "PASSIVE_INTEGRATION" ? 700 : 500,
							cursor: "pointer",
						}}
					>
						Nordic Cloud Passive Integration
					</button>

					{onClose && (
						<button
							type="button"
							onClick={onClose}
							style={{
								backgroundColor: "transparent",
								border: "none",
								color: "var(--dv-text-secondary)",
								fontSize: "1.1rem",
								cursor: "pointer",
								marginLeft: "0.5rem",
							}}
						>
							✕
						</button>
					)}
				</div>
			</div>

			{activeTab === "COLLISION_SHIELD" ? (
				/* Active Collision Shield: CalPERS vs. Bavarian Pension */
				<div style={{ display: "flex", flexDirection: "column", gap: "1rem" }}>
					{/* Side-by-Side Comparison */}
					<div
						style={{
							display: "grid",
							gridTemplateColumns: "1fr 1fr",
							gap: "1rem",
						}}
					>
						{/* Entity A: CalPERS */}
						<div
							data-testid="shield-calpers-card"
							style={{
								backgroundColor: "var(--dv-surface-2)",
								border: "1.5px solid var(--dv-hub-accent)",
								borderRadius: "0.375rem",
								padding: "0.75rem",
							}}
						>
							<div
								style={{
									display: "flex",
									justifyContent: "space-between",
									alignItems: "center",
									marginBottom: "0.4rem",
								}}
							>
								<strong
									style={{ fontSize: "0.75rem", color: "var(--dv-hub-accent)" }}
								>
									Entity A: CalPERS Master LP
								</strong>
								<span
									style={{
										fontSize: "0.65rem",
										fontWeight: 700,
										backgroundColor: "var(--dv-surface-1)",
										border: "1px solid var(--dv-hub-accent)",
										padding: "0.1rem 0.35rem",
										borderRadius: "0.2rem",
										color: "var(--dv-hub-accent)",
									}}
								>
									TIER_1_GLOBAL_REGISTRY
								</span>
							</div>

							<div
								style={{
									fontSize: "0.7rem",
									fontFamily: "ui-monospace, monospace",
									lineHeight: 1.6,
									color: "var(--dv-text-primary)",
								}}
							>
								<div>Source: investran.src_lp_investors</div>
								<div>Natural Key: lp_id = 'LP-101'</div>
								<div>BKCC Prefix: 'GLOBAL'</div>
								<div>Pre-Hash: 6:GLOBAL||6:LP-101</div>
								<div>
									Hash Digest: <strong>5D15..0AE4</strong>
								</div>
								<div>
									Entity: California Public Employees' Retirement System
								</div>
								<div style={{ color: "var(--dv-lnk-accent)", fontWeight: 700 }}>
									Commitment: $500,000,000.00
								</div>
							</div>

							<div style={{ marginTop: "0.5rem" }}>
								<code
									style={{
										fontSize: "0.6875rem",
										backgroundColor: "var(--dv-surface-1)",
										border: "1px solid var(--dv-border-strong)",
										borderRadius: "0.2rem",
										padding: "0.15rem 0.35rem",
										display: "block",
										wordBreak: "break-all",
									}}
								>
									[HK: 5D15..0AE4 | GLOBAL:LP-101 CalPERS]
								</code>
							</div>
						</div>

						{/* Entity B: Bavarian Pension */}
						<div
							data-testid="shield-bavarian-card"
							style={{
								backgroundColor: "var(--dv-surface-2)",
								border: "1.5px solid var(--dv-gate-autofix)",
								borderRadius: "0.375rem",
								padding: "0.75rem",
							}}
						>
							<div
								style={{
									display: "flex",
									justifyContent: "space-between",
									alignItems: "center",
									marginBottom: "0.4rem",
								}}
							>
								<strong
									style={{
										fontSize: "0.75rem",
										color: "var(--dv-gate-autofix)",
									}}
								>
									Entity B: Bavarian Pension Reserve Fund
								</strong>
								<span
									style={{
										fontSize: "0.65rem",
										fontWeight: 700,
										backgroundColor: "var(--dv-surface-1)",
										border: "1px solid var(--dv-gate-autofix)",
										padding: "0.1rem 0.35rem",
										borderRadius: "0.2rem",
										color: "var(--dv-gate-autofix)",
									}}
								>
									TIER_3_LOCAL_SCOPED
								</span>
							</div>

							<div
								style={{
									fontSize: "0.7rem",
									fontFamily: "ui-monospace, monospace",
									lineHeight: 1.6,
									color: "var(--dv-text-primary)",
								}}
							>
								<div>Source: legacy_gp.src_legacy_lp_roster</div>
								<div>Natural Key: lp_num = 'LP-101' (Same string!)</div>
								<div>BKCC Prefix: 'LEGACY_GP_ADMIN'</div>
								<div>Pre-Hash: 15:LEGACY_GP_ADMIN||6:LP-101</div>
								<div>
									Hash Digest: <strong>6B7C..D8B9</strong>
								</div>
								<div>Entity: Bavarian Pension Reserve Fund</div>
								<div style={{ color: "var(--dv-lnk-accent)", fontWeight: 700 }}>
									Account Balance: €25,000,000.00
								</div>
							</div>

							<div style={{ marginTop: "0.5rem" }}>
								<code
									style={{
										fontSize: "0.6875rem",
										backgroundColor: "var(--dv-surface-1)",
										border: "1px solid var(--dv-border-strong)",
										borderRadius: "0.2rem",
										padding: "0.15rem 0.35rem",
										display: "block",
										wordBreak: "break-all",
									}}
								>
									[HK: 6B7C..D8B9 | LEGACY_GP_ADMIN:LP-101 Bavarian Pension]
								</code>
							</div>
						</div>
					</div>

					{/* Collision Prevention Explanation & SAL Crosswalk */}
					<div
						data-testid="shield-sal-link"
						style={{
							backgroundColor: "var(--dv-surface-2)",
							border: "1px solid var(--dv-sal-accent)",
							borderRadius: "0.375rem",
							padding: "0.75rem",
							fontSize: "0.75rem",
						}}
					>
						<div
							style={{
								display: "flex",
								alignItems: "center",
								gap: "0.5rem",
								color: "var(--dv-sal-accent)",
								fontWeight: 700,
								marginBottom: "0.3rem",
							}}
						>
							<span>
								[SAL] Verified Same-As Link Resolution (sal_limited_partner)
							</span>
						</div>
						<p
							style={{
								margin: "0 0 0.4rem",
								color: "var(--dv-text-secondary)",
								fontSize: "0.7rem",
								lineHeight: 1.4,
							}}
						>
							Without BKCC, both records hash to <code>5D15..0AE4</code>,
							erroneously merging a $500M CalPERS commitment with a €25M
							Bavarian Pension account. With Algorithm A.5 BKCC prefixing, each
							entity receives a distinct cryptographic identity. When steward
							crosswalk <code>mdm.src_lp_steward_crosswalk</code> proves they
							belong to the same LP relationship,{" "}
							<code>sal_limited_partner</code> bridges them deterministically
							without mutating either Hub hash key.
						</p>
					</div>
				</div>
			) : (
				/* Passive Integration View */
				<div
					data-testid="passive-integration-view"
					style={{
						backgroundColor: "var(--dv-surface-2)",
						border: "1px solid var(--dv-lnk-accent)",
						borderRadius: "0.375rem",
						padding: "0.75rem",
						fontSize: "0.75rem",
					}}
				>
					<div
						style={{
							fontWeight: 700,
							color: "var(--dv-lnk-accent)",
							marginBottom: "0.4rem",
						}}
					>
						Passive Multi-Source Integration on ISO 17442 Global LEI
					</div>
					<p
						style={{
							margin: "0 0 0.5rem",
							color: "var(--dv-text-secondary)",
							fontSize: "0.7rem",
						}}
					>
						Both DealCloud CRM and Investran Fund Accounting reference Nordic
						Cloud SaaS AB via ISO 17442 LEI: <code>529900T8BM49AURSDO55</code>.
						Because LEI is a Tier-1 Global identifier (
						<code>bkcc_prefix = 'GLOBAL'</code>), both sources generate the
						exact same Hub key:
					</p>
					<code
						style={{
							display: "block",
							backgroundColor: "var(--dv-surface-1)",
							border: "1px solid var(--dv-border-strong)",
							borderRadius: "0.25rem",
							padding: "0.4rem 0.6rem",
							fontFamily: "ui-monospace, monospace",
							fontSize: "0.75rem",
							color: "var(--dv-text-primary)",
						}}
					>
						[HK: 0C18..454E | GLOBAL:529900T8BM49AURSDO55 Nordic Cloud]
					</code>
					<div
						style={{
							fontSize: "0.6875rem",
							color: "var(--dv-gate-pass)",
							marginTop: "0.4rem",
							fontWeight: 600,
						}}
					>
						✓ Zero Hub DDL modifications required when onboarding DealCloud CRM
						(EVO-05 passive integration).
					</div>
				</div>
			)}
		</section>
	);
}

export const VIZ_GATE_04_PLUGIN: WorkbenchGateCardPlugin = {
	gateId: VIZ_GATE_04_ID,
	title: "Gate 1 3-Tier BK Authority & BKCC Collision Shield",
	ruleIds: ["KEY-01", "KEY-02", "KEY-03", "KEY-04", "KEY-05", "DV-COL-012"],
	component: (props: GateCardPluginProps) => (
		<BkccCollisionShieldDrawer
			isOpen={props.isExpanded}
			onClose={props.onToggleExpand}
		/>
	),
};
