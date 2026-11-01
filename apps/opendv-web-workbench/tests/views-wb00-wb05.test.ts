/**
 * Behavioral Unit Tests for Workbench Views Part 1 (WB-00 through WB-05) (`tests/views-wb00-wb05.test.ts`)
 * Verifies plugin registration, 3-Layer progressive disclosure rendering, PDF character span grounding,
 * CaptainHoldDecisionModal submission, 100% column triage accounting, and BKCC collision shield.
 */

import { beforeEach, describe, expect, it } from "bun:test";
import {
	ALL_WORKBENCH_VIEW_IDS,
	type CaptainHoldDecisionRequest,
	PE_DEMO_EVENTS,
	type StageId,
	type VaultObservabilityEventDTO,
	type WorkbenchViewId,
} from "@opendv/protocol-ts";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { WorkbenchShell } from "../src/components/shell/WorkbenchShell";
import { WorkbenchPluginRegistry } from "../src/plugins/plugin-registry";
import {
	createWorkbenchStore,
	useWorkbenchStore,
} from "../src/store/workbench-store";
import {
	ALL_WB00_TO_WB05_PLUGINS,
	VIZ_GATE_01_PLUGIN,
	VIZ_GATE_02_PLUGIN,
	VIZ_GATE_04_PLUGIN,
	VIZ_GATE_05_PART_A_PLUGIN,
	VIZ_GATE_06_PLUGIN,
	registerWB00toWB05,
	wb00ViewPlugin,
	wb01ViewPlugin,
	wb02ViewPlugin,
	wb03ViewPlugin,
	wb04ViewPlugin,
	wb05ViewPlugin,
} from "../src/views/register-wb00-wb05";
import { MultiAgentTimeline } from "../src/views/wb00/MultiAgentTimeline";
import { SixGateFunnelCard } from "../src/views/wb00/SixGateFunnelCard";
import { WB00CommandCenter } from "../src/views/wb00/WB00CommandCenter";
import { BipartiteMapCanvas } from "../src/views/wb01/BipartiteMapCanvas";
import { CounterfactualTrapDrawer } from "../src/views/wb01/CounterfactualTrapDrawer";
import { WB01DualGraphView } from "../src/views/wb01/WB01DualGraphView";
import { FourQuadrantScatterPlot } from "../src/views/wb02/FourQuadrantScatterPlot";
import { InclusionDependencyDAG } from "../src/views/wb02/InclusionDependencyDAG";
import { WB02EmpiricalProfiler } from "../src/views/wb02/WB02EmpiricalProfiler";
import {
	CaptainHoldDecisionModal,
	DEFAULT_CAPTAIN_HOLD_DATA,
} from "../src/views/wb03/CaptainHoldDecisionModal";
import { DocumentSpanHighlighter } from "../src/views/wb03/DocumentSpanHighlighter";
import { ThreeTierERFunnel } from "../src/views/wb03/ThreeTierERFunnel";
import { WB03UnstructuredView } from "../src/views/wb03/WB03UnstructuredView";
import { ColumnAccountingSankey } from "../src/views/wb04/ColumnAccountingSankey";
import { TriageLedgerTable } from "../src/views/wb04/TriageLedgerTable";
import { WB04ColumnTriageView } from "../src/views/wb04/WB04ColumnTriageView";
import { BkccCollisionShieldDrawer } from "../src/views/wb05/BkccCollisionShieldDrawer";
import { TopologyCanvas } from "../src/views/wb05/TopologyCanvas";
import { WB05ELMTopologyView } from "../src/views/wb05/WB05ELMTopologyView";

describe("Work Package WP-11: Workbench Views Part 1 (WB-00 through WB-05)", () => {
	let registry: WorkbenchPluginRegistry;

	const getDemoEvent = (viewId: string): VaultObservabilityEventDTO => {
		const ev = PE_DEMO_EVENTS.find((e) => e.workbench_view_id === viewId);
		if (!ev) {
			throw new Error(
				`Demo event for view ${viewId} not found in PE_DEMO_EVENTS`,
			);
		}
		return ev;
	};

	beforeEach(() => {
		registry = new WorkbenchPluginRegistry();
		registerWB00toWB05(registry);
	});

	describe("1. Plugin Registration & Co-Located Gate Cards", () => {
		it("registers views WB-00 through WB-05 with valid metadata", () => {
			const expectedViews = [
				{
					id: "WB-00",
					stageId: "STAGE_00_ORCHESTRATION",
					shortTitle: "Command Center",
					scene: 1,
				},
				{
					id: "WB-01",
					stageId: "STAGE_00_ORCHESTRATION",
					shortTitle: "Dual-Graph Grounding",
					scene: 1,
				},
				{
					id: "WB-02",
					stageId: "STAGE_01_SCOUT_SOURCE",
					shortTitle: "Empirical Profiler",
					scene: 1,
				},
				{
					id: "WB-03",
					stageId: "STAGE_02_SCOUT_UNSTRUCTURED",
					shortTitle: "Unstructured PDF & ER",
					scene: 2,
				},
				{
					id: "WB-04",
					stageId: "STAGE_03_SHIP_VAULT_PASS1",
					shortTitle: "Column Triage Sankey",
					scene: 3,
				},
				{
					id: "WB-05",
					stageId: "STAGE_04_SHIP_VAULT_PASS2",
					shortTitle: "ELM Topology & BKCC",
					scene: 4,
				},
			];

			for (const expected of expectedViews) {
				const plugin = registry.getView(expected.id as WorkbenchViewId);
				expect(plugin).toBeDefined();
				expect(plugin?.stageId).toBe(expected.stageId as StageId);
				expect(plugin?.shortTitle).toBe(expected.shortTitle);
				expect(plugin?.sceneNumber).toBe(expected.scene);
				expect(typeof plugin?.component).toBe("function");
			}
		});

		it("registers co-located gate card plugins with valid rule bindings", () => {
			expect(registry.getGateCard("VIZ-GATE-01")).toBeDefined();
			expect(registry.getGateCard("VIZ-GATE-02")).toBeDefined();
			expect(registry.getGateCard("VIZ-GATE-04")).toBeDefined();
			expect(registry.getGateCard("VIZ-GATE-05")).toBeDefined();
			expect(registry.getGateCard("VIZ-GATE-06")).toBeDefined();

			const eventWithGate01 = getDemoEvent("WB-00");
			const matchedCards = registry.findGateCardsForEvent(eventWithGate01);
			expect(matchedCards.some((c) => c.gateId === "VIZ-GATE-01")).toBe(true);
		});
	});

	describe("2. WB-00: Master Pipeline Command Center & 6-Gate Scoreboard", () => {
		it("renders MultiAgentTimeline with STAGE_00 through STAGE_07 and agent personas", () => {
			const event = getDemoEvent("WB-00");
			const html = renderToStaticMarkup(
				createElement(MultiAgentTimeline, {
					currentEvent: event,
					events: [event],
				}),
			);

			expect(html).toContain('data-testid="multi-agent-timeline"');
			expect(html).toContain("STAGE_00");
			expect(html).toContain("STAGE_01");
			expect(html).toContain("STAGE_02");
			expect(html).toContain("STAGE_03");
			expect(html).toContain("STAGE_04");
			expect(html).toContain("STAGE_05");
			expect(html).toContain("STAGE_06");
			expect(html).toContain("STAGE_07");

			expect(html).toContain("@vault-first-mate");
			expect(html).toContain("@scout-source");
			expect(html).toContain("@scout-unstructured");
			expect(html).toContain("@ship-vault");
			expect(html).toContain("@axi-dv");
			expect(html).toContain("@deterministic-compiler");
		});

		it("renders SixGateFunnelCard with Gates 0 through 5 verdicts and active exception scorecard", () => {
			const event = getDemoEvent("WB-00");
			const html = renderToStaticMarkup(
				createElement(SixGateFunnelCard, {
					event,
					isExpanded: true,
				}),
			);

			expect(html).toContain('data-testid="six-gate-funnel-card"');
			expect(html).toContain("Gate 0: Profile &amp; Schema Drift");
			expect(html).toContain("Gate 1: Contract, Keys &amp; Grain");
			expect(html).toContain("Gate 2: SQLGlot AST &amp; Determinism");
			expect(html).toContain("Gate 3: NetworkX DAG Topology");
			expect(html).toContain("Gate 4: YAGNI Cost Guard &amp; Write-Amp");
			expect(html).toContain("Gate 5: DuckDB Dynamic Sandbox");

			expect(html).toContain('data-testid="active-exception-scorecard"');
			expect(html).toContain("DV-COL-012");
			expect(html).toContain("ALG-A1");
			expect(html).toContain("LEGACY_GP_ADMIN");
			expect(html).toContain("W_amp = 4.20 MB/d");
		});

		it("renders full WB00CommandCenter view with summary metrics and proof inspection trigger", () => {
			const event = getDemoEvent("WB-00");
			let inspectProofCalled = false;

			const html = renderToStaticMarkup(
				createElement(WB00CommandCenter, {
					currentEvent: event,
					eventsForView: [event],
					onInspectProof: () => {
						inspectProofCalled = true;
					},
				}),
			);

			expect(html).toContain('data-testid="wb00-command-center"');
			expect(html).toContain("Sources Onboarded");
			expect(html).toContain("67 (100%)");
			expect(html).toContain("99 / 99");
			expect(html).toContain("GLOBAL:APEX_BO_IV");
			expect(html).toContain("GLOBAL:LP-101");
			expect(html).toContain("Inspect Layer 3 Formal Proof ↗");
		});
	});

	describe("3. WB-01: Dual-Graph Grounding & Counterfactual Inspector", () => {
		it("renders BipartiteMapCanvas with CBC concepts, Bronze tables, and zero phantom hubs", () => {
			const html = renderToStaticMarkup(createElement(BipartiteMapCanvas));

			expect(html).toContain('data-testid="bipartite-map-canvas"');
			expect(html).toContain("Graph A: CBC Enterprise Ontology Concepts");
			expect(html).toContain("Graph B: Grounded Physical Bronze Tables");

			expect(html).toContain("[CBC] Fund");
			expect(html).toContain("[CBC] Limited Partner");
			expect(html).toContain("[CBC] Portfolio Company");
			expect(html).toContain("[CBC] Document");
			expect(html).toContain("[CBC] Custodian Bank");

			// Custodian Bank must be marked UNALLOCATED with 0 phantom hubs
			expect(html).toContain("[UNALLOCATED]");
			expect(html).toContain("4 / 5 (80.0%)");
			expect(html).toContain("0 (Zero-Hallucination)");
			expect(html).toContain("investran.src_pe_funds");
		});

		it("renders CounterfactualTrapDrawer with 5 Fatal Agentic Traps and side-by-side harm analysis", () => {
			const html = renderToStaticMarkup(
				createElement(CounterfactualTrapDrawer, { isOpen: true }),
			);

			expect(html).toContain('data-testid="counterfactual-trap-drawer"');
			expect(html).toContain("Trap 1: Surrogate Sequence as Hub Key");
			expect(html).toContain("Trap 2: Phantom Hub from Unallocated Concept");
			expect(html).toContain("Trap 3: Silent Single-Namespace Hub Collision");
			expect(html).toContain("Trap 4: Nullable FK in Driving Link");
			expect(html).toContain("Trap 5: High-Frequency Micro-Satellite Bloat");

			expect(html).toContain("Unconstrained LLM Anti-Pattern Action");
			expect(html).toContain("Open-DV Deterministic Resolution");
			expect(html).toContain("investran.src_lp_investors.row_seq_id");
		});

		it("renders WB01DualGraphView integrating bipartite map and counterfactual drawer", () => {
			const event = getDemoEvent("WB-01");
			const html = renderToStaticMarkup(
				createElement(WB01DualGraphView, {
					currentEvent: event,
					eventsForView: [event],
					onInspectProof: () => {},
				}),
			);

			expect(html).toContain('data-testid="wb01-dual-graph-view"');
			expect(html).toContain("WB-01: Dual-Graph Grounding");
			expect(html).toContain('data-testid="btn-toggle-trap-drawer"');
			expect(html).toContain("Hide 5 Fatal Traps");
		});
	});

	describe("4. WB-02: Empirical Column Profiler & Candidate Discovery Matrix", () => {
		it("isolates row_seq_id (Delta=1) in Quadrant 2 as surrogate trap in FourQuadrantScatterPlot", () => {
			const html = renderToStaticMarkup(createElement(FourQuadrantScatterPlot));

			expect(html).toContain('data-testid="four-quadrant-scatter-plot"');
			expect(html).toContain("[I: Natural Business Keys]");
			expect(html).toContain("[II: SURROGATE TRAP (Δ=1)]");
			expect(html).toContain("[III: Ref / Enum Codes]");
			expect(html).toContain("[IV: Descriptive Payload]");

			expect(html).toContain('data-testid="quadrant-2-surrogate-row_seq_id"');
			expect(html).toContain("row_seq_id (Δ=1)");
			expect(html).toContain("Monotonic integer surrogate");
			expect(html).toContain("Quarantined to DROPPED sink!");
		});

		it("renders InclusionDependencyDAG distinguishing mandatory FKs from Alg A.4 split link", () => {
			const html = renderToStaticMarkup(createElement(InclusionDependencyDAG));

			expect(html).toContain('data-testid="inclusion-dependency-dag"');
			expect(html).toContain("fund_code");
			expect(html).toContain("lp_id");
			expect(html).toContain("co_invest_portco_lei");

			expect(html).toContain("[Mandatory Hub FK]");
			expect(html).toContain("[ALG A.4 Split Link]");
			expect(html).toContain("[Same-As Link (SAL)]");
			expect(html).toContain("Null Rate: 71.4%");
		});

		it("renders sortable 44-column empirical profile grid across all 7 Bronze tables", () => {
			const event = getDemoEvent("WB-02");
			const html = renderToStaticMarkup(
				createElement(WB02EmpiricalProfiler, {
					currentEvent: event,
					eventsForView: [event],
					onInspectProof: () => {},
				}),
			);

			expect(html).toContain('data-testid="wb02-empirical-profiler"');
			expect(html).toContain('data-testid="profile-grid-44-columns"');
			expect(html).toContain("44 / 44 columns shown");

			// Check presence of key columns
			expect(html).toContain('data-testid="profile-row-fund_code"');
			expect(html).toContain('data-testid="profile-row-lp_id"');
			expect(html).toContain('data-testid="profile-row-row_seq_id"');
			expect(html).toContain('data-testid="profile-row-co_invest_portco_lei"');
			expect(html).toContain('data-testid="profile-row-wire_iban"');
			expect(html).toContain('data-testid="profile-row-ebitda_amount_usd"');

			// Check classification tags
			expect(html).toContain("[BUSINESS_KEY]");
			expect(html).toContain("[SURROGATE_DROP]");
			expect(html).toContain("[OPTIONAL_FK]");
			expect(html).toContain("[REF_CODE]");
			expect(html).toContain("[PAYLOAD]");
		});
	});

	describe("5. WB-03: Unstructured PDF Span-Grounding & Captain Hold", () => {
		it("highlights exact [span_start:span_end] character offsets on CalPERS side letter and Nordic Cloud IC memo", () => {
			const html = renderToStaticMarkup(createElement(DocumentSpanHighlighter));

			expect(html).toContain('data-testid="document-span-highlighter"');
			expect(html).toContain("calpers_side_letter_fund_iv_2026.pdf");
			expect(html).toContain("ic_q3_valuation_nordic_cloud.pdf");

			// Span tests on CalPERS side letter
			expect(html).toContain('data-testid="span-134-180"');
			expect(html).toContain(
				"California Public Employees&#x27; Retirement System",
			);
			expect(html).toContain('data-testid="span-227-247"');
			expect(html).toContain("5493006MHB84DD0ZWV18");
			expect(html).toContain('data-testid="span-499-506"');
			expect(html).toContain("125 bps");
			expect(html).toContain('data-testid="span-1112-1147"');
			expect(html).toContain("Horizon Sovereign Co-Invest Vehicle");
			expect(html).toContain("⚠️ [CAPTAIN HOLD]");
		});

		it("renders 3-Tier ER Confidence Funnel with Tier-U1, Tier-U2, and held Tier-U3", () => {
			const html = renderToStaticMarkup(createElement(ThreeTierERFunnel));

			expect(html).toContain('data-testid="three-tier-er-funnel"');
			expect(html).toContain('data-testid="tier-u1-card"');
			expect(html).toContain("Tier-U1: Deterministic ID");
			expect(html).toContain("3 Bound (S = 1.00)");

			expect(html).toContain('data-testid="tier-u2-card"');
			expect(html).toContain("Tier-U2: Fuzzy / String Match");
			expect(html).toContain("3 Bound (S &gt;= 0.96)");

			expect(html).toContain('data-testid="tier-u3-card"');
			expect(html).toContain("Tier-U3: Ambiguous Hold");
			expect(html).toContain("1 Held (Margin = 0.02)");
			expect(html).toContain("BLOCKED from creating hub_fund!");

			expect(html).toContain('data-testid="gate-rules-status"');
			expect(html).toContain("Rule U1");
			expect(html).toContain("Rule U2");
			expect(html).toContain("Rule U3");
			expect(html).toContain("Rule U4 + A.9b");
		});

		it("renders CaptainHoldDecisionModal with ambiguous candidate options and triggers submitDecision", async () => {
			let submittedRequest: CaptainHoldDecisionRequest | null = null;

			const modalHtml = renderToStaticMarkup(
				createElement(CaptainHoldDecisionModal, {
					isOpen: true,
					onClose: () => {},
					holdData: DEFAULT_CAPTAIN_HOLD_DATA,
					eventId: "evt-pe-004",
					runId: "run-pe-test-2026",
					onSubmit: (req) => {
						submittedRequest = req;
					},
				}),
			);

			expect(modalHtml).toContain('data-testid="captain-hold-decision-modal"');
			expect(modalHtml).toContain("Resolve Tier-U3 Ambiguous Extraction Hold");
			expect(modalHtml).toContain("hold-tier-u3-horizon-sovereign-vehicle");
			expect(modalHtml).toContain("Horizon Sovereign Co-Invest Vehicle");

			expect(modalHtml).toContain('data-testid="candidate-option-APEX_CO_I"');
			expect(modalHtml).toContain('data-testid="candidate-option-APEX_BO_IV"');
			expect(modalHtml).toContain("APEX_CO_I");
			expect(modalHtml).toContain("APEX_BO_IV");
			expect(modalHtml).toContain("Confidence: 81%");
			expect(modalHtml).toContain("Confidence: 79%");

			expect(modalHtml).toContain('data-testid="btn-submit-decision"');
		});

		it("updates Zustand store state when Captain Hold decision is resolved", async () => {
			const store = createWorkbenchStore({
				initialEvents: PE_DEMO_EVENTS,
				initialViewId: "WB-03",
				startAtZero: false,
			});

			const stateBefore = store.getState();
			expect(stateBefore.activeHoldIds.length).toBeGreaterThan(0);
			expect(stateBefore.activeHoldIds).toContain(
				"hold-tier-u3-horizon-sovereign-vehicle",
			);

			// Submit resolution
			const response = await store.getState().submitDecision({
				event_id: "evt-pe-004",
				hold_id: "hold-tier-u3-horizon-sovereign-vehicle",
				decision_type: "RESOLVE_CAPTAIN_HOLD",
				action: "APPROVE",
				selected_candidate_id: "APEX_CO_I",
				resolution_notes: "Linked to Fund I co-invest vehicle.",
			});

			expect(response.status).toBe("ACCEPTED");
			expect(response.next_stage_id).toBe("STAGE_03_SHIP_VAULT_PASS1");

			const stateAfter = store.getState();
			expect(stateAfter.activeHoldIds).not.toContain(
				"hold-tier-u3-horizon-sovereign-vehicle",
			);

			// Check that evt-pe-004 status updated to PASS
			const updatedEvt = stateAfter.events.find(
				(e) => e.event_id === "evt-pe-004",
			);
			expect(updatedEvt?.status).toBe("PASS");
			expect(updatedEvt?.layer1_story_banner.verdict_pill).toBe("PASS");
		});
	});

	describe("6. WB-04: Column Triage Sankey & Surrogate Sink", () => {
		it("renders ColumnAccountingSankey with 100% column conservation and tombstoned row_seq_id sink", () => {
			const html = renderToStaticMarkup(createElement(ColumnAccountingSankey));

			expect(html).toContain('data-testid="column-accounting-sankey"');
			expect(html).toContain("Accountability: 44 / 44 Columns (100.0%)");

			// Sources
			expect(html).toContain("investran.src_pe_funds");
			expect(html).toContain("investran.src_lp_investors");
			expect(html).toContain("investran.src_lp_commitments_and_calls");
			expect(html).toContain("dealcloud.src_portfolio_companies");

			// Dispositions
			expect(html).toContain('data-testid="triage-bucket-BUSINESS_KEY"');
			expect(html).toContain('data-testid="triage-bucket-FOREIGN_KEY_REF"');
			expect(html).toContain('data-testid="triage-bucket-DESCRIPTIVE_PAYLOAD"');
			expect(html).toContain(
				'data-testid="triage-bucket-SYSTEM_SURROGATE_DROPPED"',
			);

			// Tombstoned surrogate sink
			expect(html).toContain('data-testid="surrogate-sink-badge"');
			expect(html).toContain("[DROPPED-SURROGATE] SINK");
			expect(html).toContain(
				"Tombstoned investran.src_lp_investors.row_seq_id",
			);
			expect(html).toContain(
				"Zero surrogate pollution in enterprise hub business keys!",
			);
		});

		it("renders TriageLedgerTable with searchable columns and dropped surrogate status", () => {
			const html = renderToStaticMarkup(createElement(TriageLedgerTable));

			expect(html).toContain('data-testid="triage-ledger-table"');
			expect(html).toContain('data-testid="ledger-row-fund_code"');
			expect(html).toContain('data-testid="ledger-row-lp_id"');
			expect(html).toContain('data-testid="ledger-row-row_seq_id"');
			expect(html).toContain('data-testid="ledger-row-co_invest_portco_lei"');
			expect(html).toContain('data-testid="ledger-row-wire_iban"');

			expect(html).toContain("[SYSTEM_SURROGATE_DROPPED]");
			expect(html).toContain("DROPPED");
		});

		it("renders WB04ColumnTriageView with metrics and full triage canvas", () => {
			const event = getDemoEvent("WB-04");
			const html = renderToStaticMarkup(
				createElement(WB04ColumnTriageView, {
					currentEvent: event,
					eventsForView: [event],
					onInspectProof: () => {},
				}),
			);

			expect(html).toContain('data-testid="wb04-column-triage-view"');
			expect(html).toContain("WB-04: Pass 1 Exhaustive Column Triage Sankey");
			expect(html).toContain("Accounted Columns");
			expect(html).toContain("67 (100%)");
		});
	});

	describe("7. WB-05: ELM Topology & BKCC Collision Shield", () => {
		it("renders TopologyCanvas with WCAG AA dual-encoded entity badges ([HUB], [LNK], [SAL], [SAT], [SAT-PII])", () => {
			const html = renderToStaticMarkup(createElement(TopologyCanvas));

			expect(html).toContain('data-testid="topology-canvas"');
			expect(html).toContain("[HUB]");
			expect(html).toContain("[LNK]");
			expect(html).toContain("[SAL]");
			expect(html).toContain("[SAT]");
			expect(html).toContain("[SAT-PII]");
			expect(html).toContain("[REF]");

			// Key entities
			expect(html).toContain('data-testid="node-hub_fund"');
			expect(html).toContain('data-testid="node-hub_limited_partner"');
			expect(html).toContain('data-testid="node-hub_portfolio_company"');
			expect(html).toContain('data-testid="node-lnk_lp_commitment"');
			expect(html).toContain('data-testid="node-lnk_lp_commitment_co_invest"');
			expect(html).toContain('data-testid="node-sal_limited_partner"');
			expect(html).toContain('data-testid="node-sat_limited_partner_pii"');
			expect(html).toContain("RESTRICTED_PII");
			expect(html).toContain('data-testid="node-sat_portco_valuation_mnpi"');
		});

		it("renders BkccCollisionShieldDrawer contrasting GLOBAL:LP-101 CalPERS vs. LEGACY_GP_ADMIN:LP-101 Bavarian Pension", () => {
			const html = renderToStaticMarkup(
				createElement(BkccCollisionShieldDrawer, { isOpen: true }),
			);

			expect(html).toContain('data-testid="bkcc-collision-shield-drawer"');

			// Entity A: CalPERS
			expect(html).toContain('data-testid="shield-calpers-card"');
			expect(html).toContain("CalPERS Master LP");
			expect(html).toContain("TIER_1_GLOBAL_REGISTRY");
			expect(html).toContain("5D15..0AE4");
			expect(html).toContain("GLOBAL:LP-101");
			expect(html).toContain("$500,000,000.00");

			// Entity B: Bavarian Pension
			expect(html).toContain('data-testid="shield-bavarian-card"');
			expect(html).toContain("Bavarian Pension Reserve Fund");
			expect(html).toContain("TIER_3_LOCAL_SCOPED");
			expect(html).toContain("6B7C..D8B9");
			expect(html).toContain("LEGACY_GP_ADMIN:LP-101");
			expect(html).toContain("€25,000,000.00");

			// SAL Link explanation
			expect(html).toContain('data-testid="shield-sal-link"');
			expect(html).toContain("sal_limited_partner");
			expect(html).toContain(
				"Without BKCC, both records hash to <code>5D15..0AE4</code>",
			);
		});

		it("renders WB05ELMTopologyView integrating topology canvas and collision shield drawer", () => {
			const event = getDemoEvent("WB-05");
			const html = renderToStaticMarkup(
				createElement(WB05ELMTopologyView, {
					currentEvent: event,
					eventsForView: [event],
					onInspectProof: () => {},
				}),
			);

			expect(html).toContain('data-testid="wb05-elm-topology-view"');
			expect(html).toContain("WB-05: Pass 2/3 ELM Topology");
			expect(html).toContain("PII Satellites Isolated");
			expect(html).toContain("Key Collisions Prevented");
			expect(html).toContain("5D15..0AE4");
			expect(html).toContain("6B7C..D8B9");
		});
	});

	describe("8. Shell Integration & Dynamic View Switching", () => {
		it("renders registered WB-00 view inside WorkbenchShell canvas", () => {
			useWorkbenchStore.getState().replayAllInstant();
			useWorkbenchStore.getState().setActiveView("WB-00");

			const html = renderToStaticMarkup(
				createElement(WorkbenchShell, {
					pluginRegistry: registry,
				}),
			);

			expect(html).toContain('data-testid="registered-view-plugin-container"');
			expect(html).toContain('data-testid="wb00-command-center"');
			expect(html).toContain("WB-00: Master Pipeline Command Center");
		});

		it("renders registered WB-03 view with Captain Hold active banner inside WorkbenchShell", () => {
			useWorkbenchStore.getState().replayAllInstant();
			useWorkbenchStore.getState().setActiveView("WB-03");

			const html = renderToStaticMarkup(
				createElement(WorkbenchShell, {
					pluginRegistry: registry,
				}),
			);

			expect(html).toContain('data-testid="registered-view-plugin-container"');
			expect(html).toContain('data-testid="wb03-unstructured-view"');
			expect(html).toContain("Horizon Sovereign Co-Invest Vehicle");
		});

		it("renders registered WB-05 view with BKCC Collision Shield inside WorkbenchShell", () => {
			useWorkbenchStore.getState().replayAllInstant();
			useWorkbenchStore.getState().setActiveView("WB-05");

			const html = renderToStaticMarkup(
				createElement(WorkbenchShell, {
					pluginRegistry: registry,
				}),
			);

			expect(html).toContain('data-testid="registered-view-plugin-container"');
			expect(html).toContain('data-testid="wb05-elm-topology-view"');
			expect(html).toContain('data-testid="bkcc-collision-shield-drawer"');
			expect(html).toContain("5D15..0AE4");
			expect(html).toContain("6B7C..D8B9");
		});
	});
});
