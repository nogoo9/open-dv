/**
 * Multi-Agent Pipeline Stage Timeline (`MultiAgentTimeline.tsx`)
 * Visualizes the dual-loop state machine execution across STAGE_00 through STAGE_07 (`docs/06` §3.1).
 */

import type {
	AgentPersona,
	StageId,
	VaultObservabilityEventDTO,
	VerdictStatus,
} from "@opendv/protocol-ts";
import { VERDICT_VISUAL_GRAMMAR } from "../../theme/theme-registry";

export interface StageDefinition {
	id: StageId;
	stepNumber: number;
	name: string;
	shortName: string;
	agent: AgentPersona;
	viewId: string;
	description: string;
}

export const PIPELINE_STAGES: readonly StageDefinition[] = [
	{
		id: "STAGE_00_ORCHESTRATION",
		stepNumber: 0,
		name: "Pipeline Orchestration & Bipartite Grounding",
		shortName: "0: Orchestration",
		agent: "vault-first-mate",
		viewId: "WB-00 / WB-01",
		description: "Loads Graph A CBC ontology and dispatches scout agents.",
	},
	{
		id: "STAGE_01_SCOUT_SOURCE",
		stepNumber: 1,
		name: "Relational Source Profiling & Discovery",
		shortName: "1: Scout Source",
		agent: "scout-source",
		viewId: "WB-02 / WB-09",
		description:
			"Profiles 44 columns, detects surrogates & inclusion dependencies.",
	},
	{
		id: "STAGE_02_SCOUT_UNSTRUCTURED",
		stepNumber: 2,
		name: "Unstructured PDF Span Grounding & ER",
		shortName: "2: Scout Unstructured",
		agent: "scout-unstructured",
		viewId: "WB-03",
		description:
			"Extracts PDF spans, resolves entities, and flags Captain Holds.",
	},
	{
		id: "STAGE_03_SHIP_VAULT_PASS1",
		stepNumber: 3,
		name: "Pass 1 Exhaustive Column Triage & Surrogate Sink",
		shortName: "3: Pass 1 Triage",
		agent: "ship-vault",
		viewId: "WB-04",
		description: "100% column accountability; row_seq_id tombstoned in sink.",
	},
	{
		id: "STAGE_04_SHIP_VAULT_PASS2",
		stepNumber: 4,
		name: "Pass 2/3 ELM Topology & BKCC Collision Shield",
		shortName: "4: Pass 2 Topology",
		agent: "ship-vault",
		viewId: "WB-05",
		description:
			"Synthesizes Hubs/Links/Sats; isolates BKCC keys & PII satellites.",
	},
	{
		id: "STAGE_05_AXI_DV_STATIC_GATES",
		stepNumber: 5,
		name: "Static Structural & DAG Verification Gates",
		shortName: "5: Static Gates",
		agent: "axi-dv",
		viewId: "WB-06 / WB-07 / WB-08",
		description:
			"Runs 99 formal invariants, YAGNI merges, and TOON self-healing.",
	},
	{
		id: "STAGE_06_AXI_DV_DYNAMIC_SANDBOX",
		stepNumber: 6,
		name: "DuckDB Shadow Sandbox & Adversarial Batches",
		shortName: "6: Dynamic Sandbox",
		agent: "axi-dv",
		viewId: "WB-10",
		description:
			"Executes 4 adversarial data batches and tests bi-temporal cut-offs.",
	},
	{
		id: "STAGE_07_DETERMINISTIC_COMPILER",
		stepNumber: 7,
		name: "Deterministic Compilation to 4-Zone SQL",
		shortName: "7: SQL Compiler",
		agent: "deterministic-compiler",
		viewId: "WB-11",
		description: "Pre-Hash Stepper (Alg A.5) & compiles four-zone DDL/DML SQL.",
	},
];

export interface MultiAgentTimelineProps {
	currentEvent?: VaultObservabilityEventDTO | null;
	events?: VaultObservabilityEventDTO[];
	selectedStageId?: StageId | null;
	onSelectStage?: (stageId: StageId) => void;
}

export function MultiAgentTimeline({
	currentEvent,
	events = [],
	selectedStageId,
	onSelectStage,
}: MultiAgentTimelineProps) {
	// Group events by stage to determine status per stage
	const stageStatusMap: Record<StageId, VerdictStatus> = {
		STAGE_00_ORCHESTRATION: "PASS",
		STAGE_01_SCOUT_SOURCE: "PASS",
		STAGE_02_SCOUT_UNSTRUCTURED: "PASS",
		STAGE_03_SHIP_VAULT_PASS1: "PASS",
		STAGE_04_SHIP_VAULT_PASS2: "PASS",
		STAGE_05_AXI_DV_STATIC_GATES: "PASS",
		STAGE_06_AXI_DV_DYNAMIC_SANDBOX: "PASS",
		STAGE_07_DETERMINISTIC_COMPILER: "PASS",
	};

	for (const ev of events) {
		if (ev.stage_id && stageStatusMap[ev.stage_id] !== undefined) {
			// Prioritize non-PASS statuses (ASK-USER, REJECT-WITH-TRACE, AUTO-FIX, AUTO_REPAIRED)
			if (ev.status === "ASK-USER") {
				stageStatusMap[ev.stage_id] = "ASK-USER";
			} else if (
				ev.status === "REJECT-WITH-TRACE" &&
				stageStatusMap[ev.stage_id] !== "ASK-USER"
			) {
				stageStatusMap[ev.stage_id] = "REJECT-WITH-TRACE";
			} else if (
				(ev.status === "AUTO-FIX" || ev.status === "AUTO_REPAIRED") &&
				stageStatusMap[ev.stage_id] === "PASS"
			) {
				stageStatusMap[ev.stage_id] = ev.status;
			}
		}
	}

	const activeStage =
		selectedStageId ?? currentEvent?.stage_id ?? "STAGE_00_ORCHESTRATION";

	return (
		<section
			aria-label="Multi-Agent Stage Timeline (STAGE_00 to STAGE_07)"
			data-testid="multi-agent-timeline"
			style={{
				backgroundColor: "var(--dv-surface-1)",
				border: "1px solid var(--dv-border-subtle)",
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
					<h3
						style={{
							margin: 0,
							fontSize: "0.875rem",
							fontWeight: 600,
							color: "var(--dv-text-primary)",
						}}
					>
						Multi-Agent Dual-Loop Pipeline Timeline (STAGE_00 → STAGE_07)
					</h3>
					<span
						style={{
							fontSize: "0.75rem",
							color: "var(--dv-text-secondary)",
						}}
					>
						Autonomous agents coordinating across 8 discrete verification stages
					</span>
				</div>
				<span
					style={{
						fontFamily: "ui-monospace, monospace",
						fontSize: "0.75rem",
						padding: "0.15rem 0.5rem",
						borderRadius: "0.25rem",
						backgroundColor: "var(--dv-surface-2)",
						color: "var(--dv-text-primary)",
						border: "1px solid var(--dv-border-strong)",
					}}
				>
					Active: {activeStage}
				</span>
			</div>

			<div
				style={{
					display: "grid",
					gridTemplateColumns: "repeat(auto-fit, minmax(130px, 1fr))",
					gap: "0.5rem",
				}}
			>
				{PIPELINE_STAGES.map((stage) => {
					const isCurrent = stage.id === activeStage;
					const status = stageStatusMap[stage.id] ?? "PASS";
					const grammar =
						VERDICT_VISUAL_GRAMMAR[status] ?? VERDICT_VISUAL_GRAMMAR.PASS;

					return (
						<button
							key={stage.id}
							type="button"
							data-testid={`stage-node-${stage.id}`}
							data-stage-id={stage.id}
							aria-current={isCurrent ? "step" : undefined}
							onClick={() => onSelectStage?.(stage.id)}
							style={{
								display: "flex",
								flexDirection: "column",
								alignItems: "flex-start",
								textAlign: "left",
								backgroundColor: isCurrent
									? "var(--dv-surface-2)"
									: "var(--dv-surface-1)",
								border: isCurrent
									? "2px solid var(--dv-hub-accent)"
									: "1px solid var(--dv-border-subtle)",
								borderRadius: "0.375rem",
								padding: "0.5rem",
								cursor: "pointer",
								position: "relative",
								transition: "border-color 0.15s ease",
							}}
						>
							<div
								style={{
									display: "flex",
									justifyContent: "space-between",
									width: "100%",
									alignItems: "center",
									marginBottom: "0.25rem",
								}}
							>
								<span
									style={{
										fontSize: "0.7rem",
										fontWeight: 700,
										color: "var(--dv-text-secondary)",
									}}
								>
									STAGE_{stage.stepNumber.toString().padStart(2, "0")}
								</span>
								<span
									title={`Verdict: ${status}`}
									style={{
										fontSize: "0.7rem",
										fontWeight: 700,
										color: `var(${grammar.cssColorVar})`,
									}}
								>
									{grammar.shapeSymbol} {grammar.prefixBadge}
								</span>
							</div>

							<div
								style={{
									fontSize: "0.75rem",
									fontWeight: 600,
									color: "var(--dv-text-primary)",
									lineHeight: 1.2,
									marginBottom: "0.25rem",
								}}
							>
								{stage.shortName}
							</div>

							<div
								style={{
									fontSize: "0.6875rem",
									color: "var(--dv-text-muted)",
									fontFamily: "ui-monospace, monospace",
								}}
							>
								@{stage.agent}
							</div>
						</button>
					);
				})}
			</div>
		</section>
	);
}
