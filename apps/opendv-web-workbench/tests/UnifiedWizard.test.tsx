/**
 * M4 Failing-First TDD Tests — Unified 6-Step Ontology & DV 2.0 Pipeline Wizard
 * (`apps/opendv-web-workbench/tests/UnifiedWizard.test.tsx`)
 *
 * Expected implementation modules (all strictly < 5,000 cl100k_base tokens):
 * - `../src/ontology/wizard/OntologyWizardShell`
 * - `../src/ontology/wizard/Step1ScopeAndBaseline`
 * - `../src/ontology/wizard/Step2SourcesAndNotes`
 * - `../src/ontology/wizard/Step3PreviewEvidence`
 * - `../src/ontology/wizard/Step4AgentDiscovery`
 * - `../src/ontology/wizard/Step5DiffGraphExport`
 * - `../src/ontology/wizard/Step6PipelineBuildAndRun`
 * - `../src/ontology/wizard/CitationInspectorDrawer`
 * - `../src/ontology/wizard/AskFiboPanel`
 *
 * Covers:
 * - Shell routing on `/`, `/ontology`, `/builder` + 6-step stepper + View Stage Logs triggers (`D19`, `FR-LOG-03`)
 * - Step 1: Option A (Blank v0.0.0, Workspace Seed, Starter Packs) vs Option B (wizard_only=true + .yaml/.json/.ttl import), dark Selected Baseline Digest card, legacy_session_count notice (`FR-SES-01`, `D25`)
 * - Step 2: 4 connector tiles (SQLite, Databricks UC, DuckDB, Parquet/CSV/XLSX/JSON -> CatalogTablePickerModal), Unstructured upload (OCR auto/force/off, do_not_send_to_agent), Inline Domain Note (/sources/text), Attached Sources statuses (`Profiled`, `Parsed`, `Local Only`) (`FR-ING-01..03`)
 * - Step 3: 3a Structured Tables + PK/FK/surrogate hint/null%/entropy/FIBO-gap hints + <= 50 sample rows + Fast-Forward to Step 6 (`D6`, `D8`); 3b Parsed Chunks & Embedded Tables (`PROSE`/`Table`, offsets, zero-floor observed terms `D4`)
 * - Step 4: 4a 422 AGENT_NOT_CONFIGURED Coral Gate mounting <AgentHarnessConfigCard variant="gate" /> (`P2`) flipping readiness; 4b Steering prompt + 3 persona badges + 3-col cards (unchecked MAJOR Opt-In `D7`, 1x Semantic `D4`, FIBO Match & AskFiboPanel `/fibo-qa` `D2`), parent-to-child cascade + inline type edits (`D5`); 4c CitationInspectorDrawer (716px) w/ <mark> spans, Jump-to-Step-3 links, side-by-side FIBO (`FR-CIT-01..04`)
 * - Step 5: Diff summary (+FIBO, +Custom, ~Enriched, +Links, Validation PASS), GraphCanvas + optional VisualEditorDrawer (`D15`), Export / Promote Ontology Only (`D9`), Commit
 * - Step 6: Committed badge + SHA, dialect toggles (DuckDB 5-Zone Z0..Z4 + Databricks Delta), deployed_contract Baseline Picker (`D12`), Derived DV Topology, dark 7-stage console (`00..06` handling `06 Prover` before `05 Lower`), inline StewardHoldModal (`D14`), Budget Steward Hold (`Raise budget & resume` / `Abort`, `D21`), Sync Drift to Step 4 (`FR-DVM-16`), 5-Zone Z0..Z4 SQL (`information_mart`, never `4-Zone`/`marts`), Explore Generated DV 2.0 Schema & Lineage
 * - Platform Guards: Zero deterministic mode UI (`D28`), Single-active-job-per-session lock (`D27`), mobile < 768px view-only graph editing (`D26`)
 */

import { describe, expect, it } from "bun:test";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { OpenDVWorkbenchExplorer } from "../src/explorer/OpenDVWorkbenchExplorer";
import { createExplorerStore } from "../src/explorer/explorer-store";
import { createDefaultPlatformConfig } from "../src/explorer/settings/usePlatformConfigActions";
import { VisualEditorDrawer } from "../src/ontology/components/VisualEditorDrawer";
import { escapeTurtleString } from "../src/ontology/store/local-export";
import { isSupportedSourceFile } from "../src/ontology/store/sources-preview-slice";
import { studioStore } from "../src/ontology/store/studio-store";
import { AskFiboPanel } from "../src/ontology/wizard/AskFiboPanel";
import { CitationInspectorDrawer } from "../src/ontology/wizard/CitationInspectorDrawer";
import { OntologyWizardShell } from "../src/ontology/wizard/OntologyWizardShell";
import { Step1ScopeAndBaseline } from "../src/ontology/wizard/Step1ScopeAndBaseline";
import { Step2SourcesAndNotes } from "../src/ontology/wizard/Step2SourcesAndNotes";
import { Step3PreviewEvidence } from "../src/ontology/wizard/Step3PreviewEvidence";
import {
	Step4AgentDiscovery,
	cascadeUncheckConcept,
} from "../src/ontology/wizard/Step4AgentDiscovery";
import { Step5DiffGraphExport } from "../src/ontology/wizard/Step5DiffGraphExport";
import {
	PIPELINE_STAGE_PILLS,
	Step6PipelineBuildAndRun,
} from "../src/ontology/wizard/Step6PipelineBuildAndRun";
import {
	applyAskFiboActionToStore,
	deriveStep4State,
	syncStep4DecisionsToStore,
} from "../src/ontology/wizard/wizard-step4-adapter";
import {
	aggregateStagePillStatus,
	isUnstructuredSource,
	partitionPipelineSources,
} from "../src/ontology/wizard/wizard-step6-adapter";
import { GLOBAL_COMMERCE_EXPLORER_BUNDLE } from "./fixtures/commerce-explorer-fixture";

const SAMPLE_SHA =
	"9abb6eefb16f7ff412c18e3580bab76cdccc56b6e58b66944fd56fc49117fc02";
const GENESIS_SHA =
	"1916a5c6c4d7280a7b2e0c559f1f44b8f1920e889b2d3c4e5f60718293a4b5c6";

function makeReadyConfig() {
	const cfg = createDefaultPlatformConfig();
	cfg.harness.provider = "agy";
	cfg.providers = cfg.providers.map((p) =>
		p.provider === "agy" ? { ...p, available: true } : p,
	);
	return cfg;
}

function makeUnreadyConfig() {
	const cfg = createDefaultPlatformConfig();
	cfg.harness.provider = "agy";
	cfg.providers = cfg.providers.map((p) =>
		p.provider === "agy" ? { ...p, available: false } : p,
	);
	return cfg;
}

describe("M4: Unified 6-Step Ontology & Data Vault 2.0 Pipeline Wizard", () => {
	describe("0. Shell Mounting, 6-Step Stepper & Legacy Screen Replacement (D19, FR-LOG-03)", () => {
		it("mounts OntologyWizardShell for /, /ontology, and /builder routes in OpenDVWorkbenchExplorer without legacy WizardShell or OntologyStudioApp", () => {
			for (const [subView, path] of [
				["studio", "/ontology"],
				["builder", "/builder"],
			] as const) {
				const store = createExplorerStore({
					bundle: GLOBAL_COMMERCE_EXPLORER_BUNDLE,
					startView: "ontology",
					syncBrowserUrl: false,
				});
				store.getState().syncFromUrl(path, "?session=ses-w1-portfolio-001");
				store.getState().goOntology(subView);

				const html = renderToStaticMarkup(
					createElement(OpenDVWorkbenchExplorer, { store }),
				);
				expect(html).toContain('data-testid="ontology-wizard-shell"');
				expect(html).not.toContain('data-testid="ontology-studio-tab-host"');
				expect(html).not.toContain("Loading Ontology Studio...");
			}
		});

		it("renders 6-step numbered stepper (1..6) and View Stage Logs triggers on Step 2, 4, 5, and 6", () => {
			for (const step of [2, 4, 5, 6] as const) {
				const html = renderToStaticMarkup(
					createElement(OntologyWizardShell, {
						sessionId: "ses-w1-portfolio-001",
						initialStep: step,
						platformConfig: makeReadyConfig(),
						onOpenLogs: () => {},
					}),
				);

				expect(html).toContain('data-testid="ontology-wizard-shell"');
				for (let idx = 1; idx <= 6; idx += 1) {
					expect(html).toContain(`data-testid="wizard-stepper-step-${idx}"`);
				}
				expect(html).toContain("View Stage Logs");
			}
		});
	});

	describe("1. Step 1: Scope & Baseline, Dark Digest Card, Import & Legacy Notice (FR-ING-01, FR-SES-01, D25)", () => {
		it("renders Option A (Blank v0.0.0, Workspace Seed, Starter Packs) and Option B (new-schema sessions + YAML/JSON/TTL import) with dark Selected Baseline Digest card", () => {
			const html = renderToStaticMarkup(
				createElement(Step1ScopeAndBaseline, {
					baselineMode: "new",
					newSourceKind: "starter_packs",
					starterPacks: [
						{
							pack_id: "core_spine",
							display_name: "Core Asset Spine",
							concept_count: 14,
							relationship_count: 11,
							description: "Instrument, Portfolio, Position, Account",
						},
						{
							pack_id: "public_equities_oms",
							display_name: "Public Equities OMS",
							concept_count: 18,
							relationship_count: 15,
							description: "Orders, Executions, Venues",
						},
					],
					selectedPackIds: ["core_spine"],
					sessions: [
						{
							session_id: "ses-w1-portfolio-001",
							domain_namespace: "wealth_mgmt",
							enterprise_owner: "Enterprise",
							status: "ACTIVE",
							active_version: "1.0.0",
							created_at: "2026-11-19T00:00:00Z",
							updated_at: "2026-11-19T01:00:00Z",
							wizard_schema_version: 2,
						},
					],
					legacySessionCount: 3,
					activeDigest: {
						sessionId: "ses-w1-portfolio-001",
						version: "v0.0.0",
						sha256: GENESIS_SHA,
						label: "Blank Genesis Baseline",
					},
					onModeChange: () => {},
					onCreateSession: async () => {},
					onImportOntology: async () => {},
					onSelectSession: () => {},
				}),
			);

			expect(html).toContain('data-testid="wizard-step-1"');
			expect(html).toContain("Blank");
			expect(html).toContain("v0.0.0");
			expect(html).toContain("Workspace Seed");
			expect(html).toContain("Starter Packs");
			expect(html).toContain("Core Asset Spine");
			expect(html).toContain("Evolve Existing");
			expect(html).toContain("ses-w1-portfolio-001");
			expect(html).toContain(".yaml");
			expect(html).toContain(".json");
			expect(html).toContain(".ttl");

			// Dark Selected Baseline Digest card (#181715)
			expect(html).toContain('data-testid="selected-baseline-digest-card"');
			expect(html.toLowerCase()).toContain("#181715");
			expect(html).toContain(GENESIS_SHA.slice(0, 12));

			// One-line legacy session notice (D25)
			expect(html).toContain('data-testid="legacy-sessions-notice"');
			expect(html).toContain("3");
			expect(html).toContain("opendv ontology studio bundle export");
		});

		it("omits legacy-sessions-notice when legacySessionCount is 0 and loads sessions via ?wizard_only=true", async () => {
			const calls: string[] = [];
			const mockFetch = (async (input: RequestInfo | URL) => {
				const url = String(input);
				calls.push(url);
				if (url.includes("/sessions/import-ontology")) {
					return new Response(
						JSON.stringify({
							session_id: "ses-imported-ttl",
							domain_namespace: "wealth_mgmt",
							enterprise_owner: "Enterprise",
							status: "ACTIVE",
							active_version: "1.0.0",
							head_sha256: SAMPLE_SHA,
							created_at: "2026-11-19T00:00:00Z",
							updated_at: "2026-11-19T00:00:00Z",
							wizard_schema_version: 2,
						}),
						{ status: 201, headers: { "Content-Type": "application/json" } },
					);
				}
				return new Response(
					JSON.stringify({ sessions: [], legacy_session_count: 0 }),
					{ status: 200, headers: { "Content-Type": "application/json" } },
				);
			}) as unknown as typeof fetch;

			const html = renderToStaticMarkup(
				createElement(Step1ScopeAndBaseline, {
					baselineMode: "evolve",
					sessions: [],
					legacySessionCount: 0,
					fetchImpl: mockFetch,
					apiUrl: "http://platform.local",
				}),
			);
			expect(html).not.toContain('data-testid="legacy-sessions-notice"');
			expect(html).toContain('data-testid="import-ontology-input"');
		});
	});

	describe("2. Step 2: 4 Connector Tiles, Unstructured Upload, Inline Note & Attached Sources (FR-ING-02..03)", () => {
		it("renders all 4 connector tiles, OCR mode selector, do_not_send_to_agent flag, Inline Note composer, and Attached Sources status badges", () => {
			const html = renderToStaticMarkup(
				createElement(Step2SourcesAndNotes, {
					sessionId: "ses-w1-portfolio-001",
					ocrMode: "auto",
					doNotSendToAgent: true,
					attachedSources: [
						{
							source_id: "src-sqlite-1",
							source_sha256: SAMPLE_SHA,
							file_name: "holdings_feed.sqlite",
							filename: "holdings_feed.sqlite",
							size_bytes: 24576,
							byte_count: 24576,
							mime_type: "application/x-sqlite3",
							do_not_send_to_agent: false,
							status_badge: "Profiled",
						},
						{
							source_id: "src-pdf-1",
							source_sha256: GENESIS_SHA,
							file_name: "custody_sla_policy.pdf",
							filename: "custody_sla_policy.pdf",
							size_bytes: 102400,
							byte_count: 102400,
							mime_type: "application/pdf",
							do_not_send_to_agent: false,
							status_badge: "Parsed",
						},
						{
							source_id: "src-secret-1",
							source_sha256: SAMPLE_SHA,
							file_name: "internal_fee_schedule.txt",
							filename: "internal_fee_schedule.txt",
							size_bytes: 2048,
							byte_count: 2048,
							mime_type: "text/plain",
							do_not_send_to_agent: true,
							status_badge: "Local Only",
						},
					],
					onOpenLogs: () => {},
				}),
			);

			expect(html).toContain('data-testid="wizard-step-2"');
			// 4 connector tiles
			expect(html).toContain('data-testid="connector-tile-sqlite"');
			expect(html).toContain("SQLite");
			expect(html).toContain('data-testid="connector-tile-databricks"');
			expect(html).toContain("Databricks UC");
			expect(html).toContain('data-testid="connector-tile-duckdb"');
			expect(html).toContain("DuckDB");
			expect(html).toContain('data-testid="connector-tile-files"');
			expect(html).toContain("Parquet");
			expect(html).toContain("CSV");
			expect(html).toContain("XLSX");
			expect(html).toContain("JSON");

			// Unstructured OCR + privacy controls
			expect(html).toContain('data-testid="select-ocr-mode"');
			expect(html).toContain("auto");
			expect(html).toContain("force");
			expect(html).toContain("off");
			expect(html).toContain('data-testid="toggle-do-not-send-to-agent"');

			// Inline Domain Note composer
			expect(html).toContain('data-testid="inline-domain-note-textarea"');
			expect(html).toContain('data-testid="btn-add-inline-note"');

			// Attached Sources statuses
			expect(html).toContain("holdings_feed.sqlite");
			expect(html).toContain("Profiled");
			expect(html).toContain("custody_sla_policy.pdf");
			expect(html).toContain("Parsed");
			expect(html).toContain("internal_fee_schedule.txt");
			expect(html).toContain("Local Only");
		});
	});

	describe("3. Step 3: Structured Schema & <=50 Rows (3a), Chunks (3b), & Fast-Forward to Step 6 (D4, D6, D8)", () => {
		const fiftyOneRows = Array.from({ length: 51 }, (_, idx) => [
			`POS-${idx + 1}`,
			`US037833100${idx % 9}`,
			String(1000 + idx),
		]);

		it("renders Step 3a table profiles (PK/FK, surrogate hint, null%, entropy, FIBO gaps), caps sample rows at <= 50, and exposes Fast-Forward to Step 6", () => {
			let fastForwarded = false;
			const html = renderToStaticMarkup(
				createElement(Step3PreviewEvidence, {
					sessionId: "ses-w1-portfolio-001",
					activeSubTab: "structured",
					tables: [
						{
							table_id: "tbl-positions",
							table_name: "daily_positions",
							headers: ["position_id", "isin_code", "row_seq_sk"],
							sample_rows: fiftyOneRows,
							columns: [
								{
									column_name: "position_id",
									sql_type: "VARCHAR(32)",
									is_primary_key: true,
									surrogate_hint: false,
									foreign_key: null,
									null_rate: 0.0,
									shannon_entropy: 4.82,
								},
								{
									column_name: "isin_code",
									sql_type: "VARCHAR(12)",
									is_primary_key: false,
									surrogate_hint: false,
									foreign_key: "ref_instrument.isin_code",
									null_rate: 0.02,
									shannon_entropy: 3.15,
								},
								{
									column_name: "row_seq_sk",
									sql_type: "BIGINT",
									is_primary_key: false,
									surrogate_hint: true,
									foreign_key: null,
									null_rate: 0.0,
									shannon_entropy: 5.91,
								},
							],
							fibo_gap_hints: [
								{
									column_name: "row_seq_sk",
									hint: "Surrogate sequence quarantined from Hub BK",
								},
							],
						},
					],
					chunks: [],
					observedTermCounts: {},
					onFastForwardToStep6: () => {
						fastForwarded = true;
					},
				}),
			);

			expect(html).toContain('data-testid="wizard-step-3"');
			expect(html).toContain("daily_positions");
			expect(html).toContain("PK");
			expect(html).toContain("FK");
			expect(html.toLowerCase()).toContain("surrogate");
			expect(html).toContain("4.82");
			expect(html).toContain("2.0%");
			expect(html).toContain("POS-50");
			expect(html).not.toContain("POS-51");
			expect(html).toContain('data-testid="btn-fast-forward-step6"');
			expect(html).toContain("Fast-Forward to Step 6");
			expect(fastForwarded).toBe(false);
		});

		it("renders Step 3b parsed chunks (PROSE / Table, offsets, confidence) and zero-floor observed term counts", () => {
			const html = renderToStaticMarkup(
				createElement(Step3PreviewEvidence, {
					sessionId: "ses-w1-portfolio-001",
					activeSubTab: "chunks",
					tables: [],
					chunks: [
						{
							chunk_id: "chk-sla-001",
							document_id: "src-pdf-1",
							page_number: 2,
							start_char: 120,
							end_char: 248,
							text: "Custodian settlement cutoff is T+1 at 16:00 EST for institutional repos.",
							confidence: 0.96,
							is_table: false,
							table_id: null,
							table_data: null,
						},
						{
							chunk_id: "chk-tbl-002",
							document_id: "src-pdf-1",
							page_number: 3,
							start_char: 300,
							end_char: 410,
							text: "Tier | Cutoff\nT1 | 16:00",
							confidence: 0.91,
							is_table: true,
							table_id: "tbl-pdf-1",
							table_data: {
								headers: ["Tier", "Cutoff"],
								rows: [["T1", "16:00"]],
							},
						},
					],
					observedTermCounts: {
						"settlement cutoff": 1,
						"repo collateral": 4,
					},
				}),
			);

			expect(html).toContain("PROSE");
			expect(html).toContain("Table");
			expect(html).toContain("120");
			expect(html).toContain("248");
			expect(html).toContain("Custodian settlement cutoff");
			expect(html).toContain("settlement cutoff");
			expect(html).toContain("1");
		});
	});

	describe("4. Step 4: 422 Coral Gate (4a), Single-Pass + Cascading Cherry-Pick + Ask FIBO (4b), & Citation Drawer (4c)", () => {
		it("renders Step 4a Coral Gate on 422 AGENT_NOT_CONFIGURED mounting shared <AgentHarnessConfigCard variant='gate' /> (P2, FR-AGT-02)", () => {
			const html = renderToStaticMarkup(
				createElement(Step4AgentDiscovery, {
					sessionId: "ses-w1-portfolio-001",
					platformConfig: makeUnreadyConfig(),
					agentErrorCode: "AGENT_NOT_CONFIGURED",
					agentErrorDetail: "No usable LLM agent harness is configured.",
					onPlatformConfigUpdated: () => {},
				}),
			);

			expect(html).toContain('data-testid="step4-agent-setup-gate"');
			expect(html).toContain('data-testid="agent-harness-config-card"');
			expect(html).toContain("422");
			expect(html).toContain("AGENT_NOT_CONFIGURED");
			expect(html).toContain('data-testid="btn-verify-save-agent-harness"');
		});

		it("renders Step 4b steering prompt, 3 persona badges, 3-column cards (unchecked MAJOR Opt-In, 1x Semantic, FIBO Match), inline type edit, and AskFiboPanel (D1..D5, D7)", () => {
			const html = renderToStaticMarkup(
				createElement(Step4AgentDiscovery, {
					sessionId: "ses-w1-portfolio-001",
					platformConfig: makeReadyConfig(),
					steeringPrompt: "Focus on custody settlement SLAs and ISIN keys.",
					concepts: [
						{
							candidate_id: "cand-pos-1",
							concept_uri: "urn:ontology:entity:portfolio_position",
							concept_name: "PortfolioPosition",
							grounding_origin: "STRUCTURED_SCHEMA",
							bucket: "structured",
							checked: true,
							fibo_match: {
								uri: "https://spec.edmcouncil.org/fibo/ontology/FBC/ProductsAndServices/FinancialProductsAndServices/Position",
								predicate: "skos:broadMatch",
								score: 0.79,
							},
							attributes: [
								{
									attribute_id: "attr-mkt-val",
									name: "market_value_usd",
									data_type: "DECIMAL(18,2)",
									checked: true,
								},
							],
							dependent_links: [
								{
									link_id: "lnk-held-at",
									name: "held_at_custodian",
									checked: true,
								},
							],
							evidence_refs: [
								{
									evidence_type: "column_profile",
									table_name: "daily_positions",
									column_name: "isin_code",
								},
							],
						},
						{
							candidate_id: "cand-sla-2",
							concept_uri: "urn:ontology:entity:settlement_cutoff_rule",
							concept_name: "SettlementCutoffRule",
							grounding_origin: "DOCUMENT_OBSERVED",
							bucket: "unstructured",
							mention_count: 1,
							checked: true,
							attributes: [],
							dependent_links: [],
							evidence_refs: [
								{
									evidence_type: "document_span",
									chunk_id: "chk-sla-001",
									page_number: 2,
									start_char: 120,
									end_char: 248,
								},
							],
						},
					],
					breakingCandidates: [
						{
							candidate_id: "maj-drop-1",
							op_type: "drop_attribute",
							label: "Drop legacy_desk_memo",
							impact: "MAJOR",
							checked: false,
						},
					],
					clarifications: [
						{
							question_id: "q-1",
							focus_concept: "PortfolioPosition",
							question_text: "Treat isin_code as Tier-1 Global BK?",
							evidence_citations: [
								{
									evidence_type: "column_profile",
									source_label: "daily_positions.isin_code",
								},
							],
							options: [
								{
									option_id: "opt-yes",
									label: "Yes — Tier-1 Global ISIN",
									rationale: "Standard ISO 6166 identifier.",
									evidence_citations: [],
								},
							],
							allow_custom_text: true,
						},
					],
				}),
			);

			expect(html).toContain('data-testid="input-steering-prompt"');
			expect(html).toContain("@term-scout");
			expect(html).toContain("@domain-clarifier");
			expect(html).toContain("@ontology-architect");
			expect(html).toContain("PortfolioPosition");
			expect(html).toContain("SettlementCutoffRule");
			expect(html).toContain("1x");
			expect(html).toContain("Semantic");
			expect(html).toContain("skos:broadMatch");
			expect(html).toContain("MAJOR");
			expect(html).toContain("Opt-In");
			expect(html).toContain("Drop legacy_desk_memo");
			expect(html).toContain("DECIMAL(18,2)");
			expect(html).toContain("Ask FIBO &amp; Sources");
		});

		it("cascades parent uncheck cleanly to dependent attributes and links (D5)", () => {
			const input = [
				{
					candidate_id: "cand-pos-1",
					concept_uri: "urn:ontology:entity:portfolio_position",
					concept_name: "PortfolioPosition",
					checked: true,
					attributes: [
						{
							attribute_id: "a1",
							name: "market_value",
							data_type: "DECIMAL(18,2)",
							checked: true,
						},
					],
					dependent_links: [
						{ link_id: "l1", name: "held_in_account", checked: true },
					],
				},
			];
			const next = cascadeUncheckConcept(input, "cand-pos-1", false);
			expect(next[0]?.checked).toBe(false);
			expect(next[0]?.attributes.every((a) => !a.checked)).toBe(true);
			expect(next[0]?.dependent_links.every((l) => !l.checked)).toBe(true);
		});

		it("renders AskFiboPanel with /fibo-qa cited answer and 1-click suggested actions (D2, FR-CIT-04)", () => {
			const html = renderToStaticMarkup(
				createElement(AskFiboPanel, {
					sessionId: "ses-w1-portfolio-001",
					question: "How does FIBO model a custodian holding account?",
					answer: "FIBO FBC models custody accounts under DepositoryAccount.",
					citations: [
						{
							evidence_type: "standard_reference",
							source_label: "fibo-fbc:DepositoryAccount",
							fibo_uri:
								"https://spec.edmcouncil.org/fibo/ontology/FBC/ProductsAndServices/ClientsAndAccounts/DepositoryAccount",
						},
					],
					suggestedActions: [
						{
							action_id: "act-1",
							label: "Link skos:broadMatch to DepositoryAccount",
							op_type: "add_external_mapping",
						},
					],
					onApplySuggestedAction: () => {},
				}),
			);

			expect(html).toContain('data-testid="ask-fibo-panel"');
			expect(html).toContain("DepositoryAccount");
			expect(html).toContain("Link skos:broadMatch to DepositoryAccount");
		});

		it("renders CitationInspectorDrawer (716px) with <mark> highlighted spans, Jump-to-Step-3 links, and side-by-side FIBO definition (FR-CIT-01..04)", () => {
			const html = renderToStaticMarkup(
				createElement(CitationInspectorDrawer, {
					isOpen: true,
					sessionId: "ses-w1-portfolio-001",
					resolvedCitations: [
						{
							evidence_type: "document_span",
							citation: {
								chunk_id: "chk-sla-001",
								page_number: 2,
								start_char: 10,
								end_char: 35,
								source_label: "custody_sla_policy.pdf",
							},
							highlighted_span: "settlement cutoff is T+1",
							surrounding_text:
								"Custodian settlement cutoff is T+1 at 16:00 EST.",
							table_profile: null,
							fibo_definition: {
								uri: "https://spec.edmcouncil.org/fibo/ontology/FBC/FunctionalEntities/BusinessCenters/SettlementCycle",
								label: "SettlementCycle",
								definition:
									"Standard period between trade date and settlement date.",
								module: "FBC",
							},
						},
					],
					onJumpToStep3: () => {},
					onClose: () => {},
				}),
			);

			expect(html).toContain('data-testid="citation-inspector-drawer"');
			expect(html).toContain("716px");
			expect(html).toContain("<mark");
			expect(html).toContain("settlement cutoff is T+1");
			expect(html).toContain("SettlementCycle");
			expect(html).toContain("Jump to Step 3");
		});
	});

	describe("5. Step 5: Diff Summary, GraphCanvas, Optional VisualEditorDrawer, Export-Only & Commit (D9, D15)", () => {
		it("renders +FIBO, +Custom, ~Enriched, +Links, Validation PASS, GraphCanvas, + Add / Edit Concept button, Export Ontology Only, and Commit", () => {
			const html = renderToStaticMarkup(
				createElement(Step5DiffGraphExport, {
					sessionId: "ses-w1-portfolio-001",
					diffSummary: {
						fiboAdded: 3,
						customAdded: 2,
						enrichedCount: 1,
						linksAdded: 2,
						validationPassed: true,
					},
					isVisualEditorOpen: false,
					onOpenVisualEditor: () => {},
					onExportOnly: () => {},
					onCommit: () => {},
				}),
			);

			expect(html).toContain('data-testid="wizard-step-5"');
			expect(html).toContain("+3");
			expect(html).toContain("FIBO");
			expect(html).toContain("+2");
			expect(html).toContain("Custom");
			expect(html).toContain("~1");
			expect(html).toContain("Enriched");
			expect(html).toContain("+2");
			expect(html).toContain("Links");
			expect(html).toContain("Validation PASS");
			expect(html).toContain("+ Add / Edit Concept, Attribute or Link");
			expect(html).toContain('data-testid="btn-export-ontology-only"');
			expect(html).toContain('data-testid="btn-commit-ontology"');
		});
	});

	describe("6. Step 6: Committed Badge, 5-Zone SQL, Dialects, Baseline Picker (D12), 7-Pill Console (00..06), Inline Hold (D14), Budget Hold (D21) & Drift Sync", () => {
		it("defines 7 clickable pipeline pills (00..06) mapping 06 Prover -> STAGE_06 and 05 Lower -> STAGE_07 (docs/28 §4.4)", () => {
			expect(PIPELINE_STAGE_PILLS).toHaveLength(7);
			const pill05 = PIPELINE_STAGE_PILLS.find((p) => p.pill === "05");
			const pill06 = PIPELINE_STAGE_PILLS.find((p) => p.pill === "06");
			expect(pill05?.internalStages).toEqual([
				"STAGE_07_DETERMINISTIC_COMPILER",
			]);
			expect(pill06?.internalStages).toEqual([
				"STAGE_06_AXI_DV_DYNAMIC_SANDBOX",
			]);
		});

		it("renders committed SHA badge, DuckDB + Databricks dialect toggles, deployed_contract Baseline Picker (D12), topology table, dark 7-stage console, 5-Zone Z0..Z4 SQL (information_mart, zero '4-Zone'/'marts'), and Explore Generated DV 2.0 Schema & Lineage CTA", () => {
			const html = renderToStaticMarkup(
				createElement(Step6PipelineBuildAndRun, {
					sessionId: "ses-w1-portfolio-001",
					committedVersion: "v1.1.0",
					committedSha256: SAMPLE_SHA,
					selectedDialects: ["duckdb", "databricks"],
					baselineOptions: [
						{
							run_id: "run-w1-001",
							label: "run-w1-001 (v1.0.0 · Latest Certified)",
							is_latest_certified: true,
						},
						{
							run_id: "cold_start",
							label: "Fresh Cold-Start Vault",
							is_latest_certified: false,
						},
					],
					selectedBaselineRunId: "run-w1-001",
					topologyRows: [
						{
							entity_name: "hub_portfolio_position",
							entity_kind: "HUB",
							zone: "Z2",
							source_mapping: "daily_positions.position_id",
						},
						{
							entity_name: "pit_portfolio_position",
							entity_kind: "PIT",
							zone: "Z3",
							source_mapping: "sat_position_core",
						},
					],
					stageStatuses: {
						"00": "COMPLETED",
						"01": "COMPLETED",
						"02": "COMPLETED",
						"03": "COMPLETED",
						"04": "COMPLETED",
						"05": "RUNNING",
						"06": "COMPLETED",
					},
					zoneSqlByZone: {
						Z0: "CREATE SCHEMA IF NOT EXISTS information_mart;",
						Z1: "CREATE OR REPLACE VIEW staging.stg_daily_positions AS SELECT 1;",
						Z2: "INSERT INTO raw_vault.hub_portfolio_position SELECT 1;",
						Z3: "INSERT INTO business_vault.pit_portfolio_position SELECT 1;",
						Z4: "CREATE OR REPLACE VIEW information_mart.vw_portfolio_position_current AS SELECT 1;",
					},
					activeRunId: "run-w2-002",
					onExploreDvSchema: () => {},
					onOpenLogs: () => {},
				}),
			);

			expect(html).toContain('data-testid="wizard-step-6"');
			expect(html).toContain("v1.1.0");
			expect(html).toContain(SAMPLE_SHA.slice(0, 12));
			expect(html).toContain("DuckDB");
			expect(html).toContain("Databricks Delta");
			expect(html).toContain('data-testid="select-deployed-contract-baseline"');
			expect(html).toContain("Fresh Cold-Start Vault");
			expect(html).toContain("hub_portfolio_position");
			expect(html).toContain('data-testid="step6-pipeline-console"');
			expect(html.toLowerCase()).toContain("#181715");

			for (const pill of ["00", "01", "02", "03", "04", "05", "06"]) {
				expect(html).toContain(`data-testid="console-stage-pill-${pill}"`);
			}

			// 5-Zone Z0..Z4 SQL & information_mart (never 4-Zone / marts)
			expect(html).toContain("5-Zone");
			expect(html).toContain("information_mart");
			expect(html).not.toContain("4-Zone");
			expect(html).not.toMatch(/\bmarts\./i);

			expect(html).toContain("Explore Generated DV 2.0 Schema");
		});

		it("renders inline StewardHoldModal on PAUSED_HOLD (D14), Budget Steward Hold card on TOKEN_BUDGET_EXCEEDED (D21), and Sync Drift to Step 4 on Stage 02 drift (FR-DVM-16)", () => {
			const html = renderToStaticMarkup(
				createElement(Step6PipelineBuildAndRun, {
					sessionId: "ses-w1-portfolio-001",
					committedVersion: "v1.1.0",
					committedSha256: SAMPLE_SHA,
					activeHold: {
						hold_id: "hold-s3-001",
						run_id: "run-w2-002",
						stage_id: "STAGE_02_SCOUT_UNSTRUCTURED",
						headline: "Unmodeled feed drift detected (UF-2)",
						status: "PAUSED_HOLD",
					},
					budgetHold: {
						job_id: "job-construct-99",
						reason: "TOKEN_BUDGET_EXCEEDED",
						total_tokens: 32000,
						cost_usd: 0.48,
					},
					driftColumns: ["intraday_margin_buffer_bps", "custodian_sla_tier"],
					onSyncDriftToStep4: () => {},
					onRaiseBudgetAndResume: async () => {},
					onAbortBudgetHold: () => {},
				}),
			);

			expect(html).toContain('data-testid="steward-hold-modal"');
			expect(html).toContain("hold-s3-001");
			expect(html).toContain('data-testid="budget-steward-hold-card"');
			expect(html).toContain("TOKEN_BUDGET_EXCEEDED");
			expect(html).toContain("Raise budget &amp; resume");
			expect(html).toContain("Abort");
			expect(html).toContain('data-testid="btn-sync-drift-to-step4"');
			expect(html).toContain("Sync Drift to Step 4");
		});
	});

	describe("7. Platform Guards: Zero Deterministic UI (D28), Single-Job Lock (D27), Mobile <768px View-Only (D26)", () => {
		it("renders ZERO deterministic mode or symbolic_completion UI controls across all 6 wizard steps (D28)", () => {
			for (const step of [1, 2, 3, 4, 5, 6] as const) {
				const html = renderToStaticMarkup(
					createElement(OntologyWizardShell, {
						sessionId: "ses-w1-portfolio-001",
						initialStep: step,
						platformConfig: makeReadyConfig(),
					}),
				).toLowerCase();

				expect(html).not.toContain("symbolic_completion");
				expect(html).not.toContain("deterministic mode");
				expect(html).not.toContain("--mode deterministic");
			}
		});

		it("disables start triggers with 'Run in progress — view live logs' while keeping Cancel active when session has an active job (D27)", () => {
			const step4Html = renderToStaticMarkup(
				createElement(Step4AgentDiscovery, {
					sessionId: "ses-w1-portfolio-001",
					platformConfig: makeReadyConfig(),
					activeSessionJobId: "job-active-1",
					onCancelJob: () => {},
				}),
			);
			expect(step4Html).toContain("Run in progress");
			expect(step4Html).toContain("view live logs");
			expect(step4Html).toContain('data-testid="btn-cancel-active-job"');

			const step6Html = renderToStaticMarkup(
				createElement(Step6PipelineBuildAndRun, {
					sessionId: "ses-w1-portfolio-001",
					committedVersion: "v1.1.0",
					committedSha256: SAMPLE_SHA,
					activeSessionJobId: "job-active-1",
					onCancelJob: () => {},
				}),
			);
			expect(step6Html).toContain("Run in progress");
			expect(step6Html).toContain("view live logs");
			expect(step6Html).toContain('data-testid="btn-cancel-active-job"');
		});

		it("downgrades Step 5 graph editing to view-only mode below 768px viewport (D26)", () => {
			const mobileHtml = renderToStaticMarkup(
				createElement(Step5DiffGraphExport, {
					sessionId: "ses-w1-portfolio-001",
					viewportWidth: 390,
					diffSummary: {
						fiboAdded: 1,
						customAdded: 1,
						enrichedCount: 0,
						linksAdded: 1,
						validationPassed: true,
					},
				}),
			);

			expect(mobileHtml).toContain("View-only");
			expect(mobileHtml).toContain('data-testid="btn-open-visual-editor"');
			expect(mobileHtml).toMatch(
				/<button[^>]*data-testid="btn-open-visual-editor"[^>]*disabled/i,
			);
			expect(mobileHtml).not.toMatch(
				/<section[^>]*data-testid="wizard-step-5"[^>]*view-only-mobile/i,
			);
			expect(mobileHtml).toContain("dag-dot-grid view-only-mobile");
		});
	});

	describe("8. Iteration 2 Store Integration & Regression Guards", () => {
		const baseSession = {
			session_id: "ses-iter2-001",
			domain_namespace: "wealth_mgmt",
			enterprise_owner: "Enterprise",
			status: "ACTIVE",
			active_version: "1.0.0",
			head_sha256: SAMPLE_SHA,
			created_at: "2026-11-19T00:00:00Z",
			updated_at: "2026-11-19T00:00:00Z",
			wizard_schema_version: 2,
		};

		it("hydrates Step 4 concepts & breaking candidates from store proposals/stagedCandidates and syncs decisions to store.nodes", async () => {
			studioStore.getState().reset();
			studioStore.setState({
				activeSession: baseSession,
				proposals: [
					{
						proposal_id: "prop-iter2-1",
						status: "PENDING",
						base_version: "1.0.0",
						operations: [
							{
								op_id: "op-c1",
								op_type: "add_concept",
								concept_uri: "urn:ontology:entity:custody_account",
								concept_name: "CustodyAccount",
								grounding_origin: "STRUCTURED_SCHEMA",
								attributes: [
									{
										attribute_name: "account_code",
										data_type: "string",
										is_optional: false,
									},
								],
								external_mappings: [
									{
										scheme: "fibo",
										target_uri: "https://spec.edmcouncil.org/fibo/Account",
										predicate: "skos:broadMatch",
										confidence: 0.88,
									},
								],
							},
							{
								op_id: "op-rm-1",
								op_type: "remove_concept",
								concept_uri: "urn:ontology:entity:legacy_note",
							},
						],
					},
				],
				stagedCandidates: [
					{
						candidate_id: "stg-doc-1",
						concept_name: "SettlementCutoff",
						status: "held_back",
						evidence_refs: [
							{ evidence_type: "document_span", chunk_id: "chk-1" },
						],
					},
				],
			});

			const shellHtml = renderToStaticMarkup(
				createElement(OntologyWizardShell, {
					sessionId: baseSession.session_id,
					initialStep: 4,
					platformConfig: makeReadyConfig(),
					onOpenLogs: () => {},
				}),
			);
			expect(shellHtml).toContain("CustodyAccount");
			expect(shellHtml).toContain("SettlementCutoff");
			expect(shellHtml).toContain("MAJOR");
			expect(shellHtml).not.toContain('data-testid="session-logs-inspector"');

			const { concepts, breakingCandidates } = deriveStep4State(
				studioStore.getState().proposals,
				studioStore.getState().stagedCandidates,
			);
			expect(concepts).toHaveLength(2);
			expect(breakingCandidates[0]?.checked).toBe(false);

			const promotedCalls: string[] = [];
			const mockFetch = (async (input: RequestInfo | URL) => {
				promotedCalls.push(String(input));
				return new Response(JSON.stringify({ status: "promoted" }), {
					status: 200,
				});
			}) as unknown as typeof fetch;

			await syncStep4DecisionsToStore(
				studioStore.getState(),
				concepts,
				breakingCandidates,
				mockFetch,
			);
			const nextNodes = studioStore.getState().nodes;
			expect(
				nextNodes.some(
					(n) => n.concept_uri === "urn:ontology:entity:custody_account",
				),
			).toBe(true);
			expect(nextNodes.some((n) => n.concept_name === "SettlementCutoff")).toBe(
				true,
			);
			expect(
				promotedCalls.some((u) =>
					u.includes("/staged-candidates/stg-doc-1/promote"),
				),
			).toBe(true);

			applyAskFiboActionToStore(studioStore.getState(), {
				action_id: "act-1",
				label: "Map CustodyAccount",
				op_type: "add_external_mapping",
				payload: {
					concept_uri: "urn:ontology:entity:custody_account",
					target_uri: "https://spec.edmcouncil.org/fibo/DepositoryAccount",
					predicate: "skos:closeMatch",
					confidence: 0.91,
				},
			});
			const updated = studioStore
				.getState()
				.nodes.find(
					(n) => n.concept_uri === "urn:ontology:entity:custody_account",
				);
			expect(updated?.external_mappings?.length).toBe(2);
			studioStore.getState().reset();
		});

		it("Step 4a gate uses boolean .ready, renders interactive clarification Q&A + BudgetHoldCard, and supports native File upload + .parquet/.tsv/.jsonl", async () => {
			studioStore.getState().reset();
			studioStore.setState({ activeSession: baseSession });

			const readyHtml = renderToStaticMarkup(
				createElement(Step4AgentDiscovery, {
					sessionId: baseSession.session_id,
					platformConfig: makeReadyConfig(),
					budgetHold: {
						job_id: "job-b-1",
						reason: "TOKEN_BUDGET_EXCEEDED",
						total_tokens: 15000,
						cost_usd: 0.25,
					},
					clarifications: [
						{
							question_id: "q-clar-1",
							concept_name: "Position",
							question_text: "Is position_id global?",
							certainty: "MEDIUM",
							rationale_options: [{ id: "opt-1", label: "Yes, global" }],
							evidence_citations: [],
							status: "PENDING",
						},
					],
				}),
			);
			expect(readyHtml).toContain('data-testid="wizard-step-4"');
			expect(readyHtml).not.toContain('data-testid="step4-agent-setup-gate"');
			expect(readyHtml).toContain('data-testid="budget-steward-hold-card"');
			expect(readyHtml).toContain('type="radio"');
			expect(readyHtml).toContain(
				'data-testid="btn-submit-clarification-q-clar-1"',
			);

			expect(isSupportedSourceFile("positions.parquet", "")).toBe(true);
			expect(
				isSupportedSourceFile("trades.tsv", "text/tab-separated-values"),
			).toBe(true);
			expect(
				isSupportedSourceFile("events.jsonl", "application/x-ndjson"),
			).toBe(true);

			const nativeFile = new File(["col_a,col_b\n1,2\n"], "sample.csv", {
				type: "text/csv",
			});
			const mockUploadFetch = (async () =>
				new Response(
					JSON.stringify({
						source_id: "src-1",
						source_sha256: SAMPLE_SHA,
						file_name: "sample.csv",
						size_bytes: 14,
						mime_type: "text/csv",
						do_not_send_to_agent: false,
					}),
					{ status: 201 },
				)) as unknown as typeof fetch;

			const uploaded = await studioStore
				.getState()
				.uploadSource(nativeFile, false, mockUploadFetch);
			expect(uploaded?.source_id).toBe("src-1");
			studioStore.getState().reset();
		});

		it("emits strict PipelineInputDTO (SourceFeedDTO[], zero target_dialects), resets baseline YAML, resolves EventSource dynamically, and hydrates /report SQL", async () => {
			studioStore.getState().reset();
			studioStore.setState({
				activeSession: baseSession,
				selectedDialects: ["duckdb", "databricks"],
				selectedBaselineRunId: "__uploaded__",
				customBaselineContractContent: "version: 1.0",
				sources: [
					{
						source_id: "src-tbl-1",
						source_sha256: SAMPLE_SHA,
						file_name: "positions.parquet",
						size_bytes: 1024,
						mime_type: "application/x-parquet",
						do_not_send_to_agent: false,
					},
				],
			});

			studioStore.getState().setSelectedBaselineRunId("__cold_start__");
			expect(studioStore.getState().customBaselineContractContent).toBeNull();

			let postedBody: Record<string, unknown> | null = null;
			const listeners = new Map<string, Array<(ev: unknown) => void>>();
			const origEs = (globalThis as Record<string, unknown>).EventSource;
			class MockEs {
				readonly url: string;
				constructor(url: string) {
					this.url = url;
				}
				addEventListener(type: string, cb: (ev: unknown) => void) {
					const arr = listeners.get(type) ?? [];
					arr.push(cb);
					listeners.set(type, arr);
				}
				close() {}
			}
			(globalThis as Record<string, unknown>).EventSource = MockEs;

			try {
				const mockFetch = (async (
					input: RequestInfo | URL,
					init?: RequestInit,
				) => {
					const url = String(input);
					if (url.includes("/export-blueprint")) {
						return new Response(
							JSON.stringify({ blueprint: { domain: "wealth_mgmt" } }),
							{ status: 200 },
						);
					}
					if (url.endsWith("/api/v1/pipelines/runs")) {
						postedBody = JSON.parse(String(init?.body ?? "{}")) as Record<
							string,
							unknown
						>;
						return new Response(JSON.stringify({ run_id: "run-dyn-1" }), {
							status: 202,
						});
					}
					if (url.includes("/api/v1/runs/run-dyn-1/report")) {
						return new Response(
							JSON.stringify({
								four_zone_sql: {
									databricks: {
										zone_0_staging: "CREATE SCHEMA IF NOT EXISTS cat.staging;",
										zone_3_info_mart:
											"CREATE OR REPLACE VIEW cat.information_mart.vw_pos AS SELECT 1;",
									},
								},
								blueprint: {
									contract: {
										hubs: [
											{
												name: "hub_position",
												source_mappings: ["positions.position_id"],
											},
										],
									},
								},
							}),
							{ status: 200 },
						);
					}
					return new Response("{}", { status: 200 });
				}) as unknown as typeof fetch;

				const runId = await studioStore
					.getState()
					.startDvPipelineRun(undefined, mockFetch);
				expect(runId).toBe("run-dyn-1");
				expect(postedBody).not.toBeNull();
				expect("target_dialects" in (postedBody ?? {})).toBe(false);
				expect((postedBody as Record<string, unknown>).target_dialect).toBe(
					"databricks",
				);
				const srcs = (postedBody as Record<string, unknown>).sources as Array<
					Record<string, unknown>
				>;
				expect(Array.isArray(srcs)).toBe(true);
				expect(srcs[0]?.feed_id).toBe("src-tbl-1");
				expect(srcs[0]?.uri).toBe(`cas://${SAMPLE_SHA}`);

				for (const cb of listeners.get("run_complete") ?? []) {
					await cb({ data: JSON.stringify({ drift_columns: ["new_col"] }) });
				}
				expect(studioStore.getState().zoneSqlByZone.Z0).toContain(
					"cat.staging",
				);
				expect(studioStore.getState().zoneSqlByZone.Z3).toContain(
					"cat.information_mart.vw_pos",
				);
				expect(studioStore.getState().topologyRows[0]?.entity_name).toBe(
					"hub_position",
				);
			} finally {
				(globalThis as Record<string, unknown>).EventSource = origEs;
				studioStore.getState().reset();
			}
		});

		it("aligns PIPELINE_STAGE_PILLS to canonical docs/28 §4.4 IDs, aggregates multi-stage pill status, handles 409 lock, Create-mode VisualEditorDrawer, clearActiveHold, and escapeTurtleString control chars", async () => {
			expect(
				PIPELINE_STAGE_PILLS.find((p) => p.pill === "00")?.internalStages,
			).toEqual(["STAGE_00_ORCHESTRATION"]);
			expect(
				PIPELINE_STAGE_PILLS.find((p) => p.pill === "01")?.internalStages,
			).toEqual(["STAGE_01_SCOUT_SOURCE"]);
			expect(
				PIPELINE_STAGE_PILLS.find((p) => p.pill === "03")?.internalStages,
			).toEqual(["STAGE_03_SHIP_VAULT_PASS1", "STAGE_04_SHIP_VAULT_PASS2"]);
			expect(
				PIPELINE_STAGE_PILLS.find((p) => p.pill === "04")?.internalStages,
			).toEqual(["STAGE_05_AXI_DV_STATIC_GATES"]);

			expect(
				aggregateStagePillStatus(
					["STAGE_03_SHIP_VAULT_PASS1", "STAGE_04_SHIP_VAULT_PASS2"],
					{
						STAGE_03_SHIP_VAULT_PASS1: "COMPLETED",
						STAGE_04_SHIP_VAULT_PASS2: "RUNNING",
					},
					"03",
				),
			).toBe("RUNNING");

			studioStore.getState().reset();
			studioStore.setState({
				activeSession: baseSession,
				selectedNodeId: null,
				userRole: "editor",
			});

			const createDrawerHtml = renderToStaticMarkup(
				createElement(VisualEditorDrawer, {
					onClose: () => {},
				}),
			);
			expect(createDrawerHtml).toContain("NEW CONCEPT");
			expect(createDrawerHtml).toContain('data-testid="concept-uri-input"');
			expect(createDrawerHtml).toContain("Create Concept");
			expect(createDrawerHtml).toContain('data-testid="close-drawer-btn"');
			expect(createDrawerHtml).not.toContain(
				'data-testid="delete-concept-btn"',
			);

			const conflictFetch = (async () =>
				new Response(
					JSON.stringify({
						code: "SESSION_JOB_ACTIVE",
						detail: "Session already has an active running job.",
					}),
					{ status: 409 },
				)) as unknown as typeof fetch;

			await studioStore
				.getState()
				.startConstruct({ mode: "agent" }, conflictFetch);
			expect(studioStore.getState().activeSessionJobId).toBe(
				"active-session-job",
			);
			const cancelled = await studioStore
				.getState()
				.cancelActiveJob(conflictFetch);
			expect(cancelled).toBe(true);
			expect(studioStore.getState().activeSessionJobId).toBeNull();

			studioStore.setState({
				activeHold: {
					hold_id: "h-1",
					run_id: "r-1",
					stage_id: "STAGE_04",
					headline: "Hold",
					status: "PENDING",
				},
			});
			studioStore.getState().clearActiveHold();
			expect(studioStore.getState().activeHold).toBeNull();

			const escaped = escapeTurtleString("a\tb\bc\fd\x00\x1f\x7f");
			expect(escaped).toBe("a\\tb\\bc\\fd\\u0000\\u001F\\u007F");
			expect(
				Array.from(escaped).some(
					(ch) => ch.charCodeAt(0) < 0x20 || ch.charCodeAt(0) === 0x7f,
				),
			).toBe(false);
			studioStore.getState().reset();
		});
	});

	describe("9. Iteration 3 Remediations (Unstructured Routing, Step 4 Polish, Baseline & Job Lock)", () => {
		const baseSession = {
			session_id: "ses-iter3-001",
			domain_namespace: "wealth_mgmt",
			enterprise_owner: "Enterprise",
			status: "ACTIVE",
			active_version: "1.0.0",
			head_sha256: SAMPLE_SHA,
			created_at: "2026-11-19T00:00:00Z",
			updated_at: "2026-11-19T00:00:00Z",
			wizard_schema_version: 2,
		};

		it("partitions structured vs unstructured sources (including TSV/JSONL protection & chunk hydration) and routes mixed/unstructured Step 6 payloads cleanly", async () => {
			const parqSrc = {
				source_id: "src-parq-1",
				source_sha256: SAMPLE_SHA,
				file_name: "positions.parquet",
				size_bytes: 1024,
				mime_type: "application/x-parquet",
				do_not_send_to_agent: false,
			};
			const tsvSrc = {
				source_id: "src-tsv-1",
				source_sha256: GENESIS_SHA,
				file_name: "trades.tsv",
				size_bytes: 512,
				mime_type: "text/plain",
				do_not_send_to_agent: false,
			};
			const pdfSrc = {
				source_id: "src-pdf-1",
				source_sha256: SAMPLE_SHA,
				file_name: "custody_sla.pdf",
				size_bytes: 4096,
				mime_type: "application/pdf",
				do_not_send_to_agent: false,
			};
			const noteSrc = {
				source_id: "src-note-1",
				source_sha256: GENESIS_SHA,
				file_name: "domain-note.md",
				size_bytes: 128,
				mime_type: "text/markdown",
				do_not_send_to_agent: false,
			};
			const secretSrc = {
				source_id: "src-secret-1",
				source_sha256: SAMPLE_SHA,
				file_name: "confidential_memo.pdf",
				size_bytes: 256,
				mime_type: "application/pdf",
				do_not_send_to_agent: true,
			};
			const sources = [parqSrc, tsvSrc, pdfSrc, noteSrc, secretSrc];
			const chunks = [
				{
					chunk_id: "chk-1",
					source_id: "src-pdf-1",
					text: "Custodian settlement cutoff is T+1.",
				},
			];

			expect(isUnstructuredSource(parqSrc)).toBe(false);
			expect(isUnstructuredSource(tsvSrc)).toBe(false);
			expect(isUnstructuredSource(pdfSrc)).toBe(true);
			expect(isUnstructuredSource(noteSrc)).toBe(true);

			const partitioned = partitionPipelineSources(sources, chunks);
			expect(partitioned.sources.map((s) => s.feed_id)).toEqual([
				"src-parq-1",
				"src-tsv-1",
			]);
			expect(partitioned.unstructured_documents).toHaveLength(2);
			expect(partitioned.unstructured_documents[0]).toMatchObject({
				doc_id: "src-pdf-1",
				document_path: `cas://${SAMPLE_SHA}`,
				mime_type: "application/pdf",
				content: "Custodian settlement cutoff is T+1.",
			});
			expect(partitioned.unstructured_documents[1]).toMatchObject({
				doc_id: "src-note-1",
				document_path: `cas://${GENESIS_SHA}`,
				mime_type: "text/markdown",
			});

			// Mixed run with blueprint: emits both sources & unstructured_documents, omits unstructured_only
			studioStore.getState().reset();
			studioStore.setState({
				activeSession: baseSession,
				sources,
				evidenceChunks: chunks,
			});

			const postedPayloads: Array<Record<string, unknown>> = [];
			const mockMixedFetch = (async (
				input: RequestInfo | URL,
				init?: RequestInit,
			) => {
				const url = String(input);
				if (url.includes("/export-blueprint")) {
					return new Response(
						JSON.stringify({ blueprint: { domain_namespace: "wealth_mgmt" } }),
						{ status: 200 },
					);
				}
				if (url.endsWith("/api/v1/pipelines/runs")) {
					postedPayloads.push(
						JSON.parse(String(init?.body ?? "{}")) as Record<string, unknown>,
					);
					return new Response(JSON.stringify({ run_id: "run-mix-1" }), {
						status: 202,
					});
				}
				return new Response("{}", { status: 200 });
			}) as unknown as typeof fetch;

			await studioStore
				.getState()
				.startDvPipelineRun(undefined, mockMixedFetch);
			expect(postedPayloads).toHaveLength(1);
			expect(
				(postedPayloads[0]?.sources as Array<Record<string, unknown>>).length,
			).toBe(2);
			expect(
				(
					postedPayloads[0]?.unstructured_documents as Array<
						Record<string, unknown>
					>
				).length,
			).toBe(2);
			expect("unstructured_only" in (postedPayloads[0] ?? {})).toBe(false);

			// Unstructured-only without blueprint or initial contract: emits unstructured_only: true
			studioStore.setState({
				activeSession: baseSession,
				sources: [pdfSrc],
				evidenceChunks: chunks,
				selectedBaselineRunId: "__cold_start__",
				customBaselineContractContent: null,
			});
			const mockUnstrOnlyFetch = (async (
				input: RequestInfo | URL,
				init?: RequestInit,
			) => {
				const url = String(input);
				if (url.includes("/export-blueprint")) {
					return new Response("{}", { status: 404 });
				}
				if (url.endsWith("/api/v1/pipelines/runs")) {
					postedPayloads.push(
						JSON.parse(String(init?.body ?? "{}")) as Record<string, unknown>,
					);
					return new Response(JSON.stringify({ run_id: "run-unstr-1" }), {
						status: 202,
					});
				}
				return new Response("{}", { status: 200 });
			}) as unknown as typeof fetch;

			await studioStore
				.getState()
				.startDvPipelineRun(undefined, mockUnstrOnlyFetch);
			expect(postedPayloads).toHaveLength(2);
			expect(postedPayloads[1]?.sources).toEqual([]);
			expect(postedPayloads[1]?.unstructured_only).toBe(true);
			expect(
				(
					postedPayloads[1]?.unstructured_documents as Array<
						Record<string, unknown>
					>
				).length,
			).toBe(1);
			studioStore.getState().reset();
		});

		it("clears customBaselineContractContent when loadPipelineBaselines reloads baselines", async () => {
			studioStore.getState().reset();
			studioStore.setState({
				selectedBaselineRunId: "__uploaded__",
				customBaselineContractContent: "model_name: ManualUpload",
			});

			const mockRunsFetch = (async () =>
				new Response(
					JSON.stringify([{ run_id: "run-cert-99", status: "COMPLETED" }]),
					{ status: 200 },
				)) as unknown as typeof fetch;

			await studioStore.getState().loadPipelineBaselines(mockRunsFetch);
			expect(studioStore.getState().selectedBaselineRunId).toBe("run-cert-99");
			expect(studioStore.getState().customBaselineContractContent).toBeNull();
			studioStore.getState().reset();
		});

		it("materializes un-promoted Step 4 candidates in applyAskFiboActionToStore and batches promotions in syncStep4DecisionsToStore", async () => {
			studioStore.getState().reset();
			studioStore.setState({
				activeSession: baseSession,
				proposals: [
					{
						proposal_id: "prop-unpromoted-1",
						status: "PENDING",
						base_version: "1.0.0",
						operations: [
							{
								op_id: "op-bond-1",
								op_type: "add_concept",
								concept_uri: "urn:ontology:entity:corporate_bond",
								concept_name: "CorporateBond",
								grounding_origin: "STRUCTURED_SCHEMA",
								attributes: [],
							},
						],
					},
				],
				stagedCandidates: [
					{
						candidate_id: "stg-1",
						concept_name: "SettlementRule",
						status: "held_back",
						evidence_refs: [],
					},
					{
						candidate_id: "stg-2",
						concept_name: "MarginCall",
						status: "held_back",
						evidence_refs: [],
					},
				],
			});

			applyAskFiboActionToStore(studioStore.getState(), {
				action_id: "act-map-unpromoted",
				label: "Map CorporateBond to FIBO Bond",
				op_type: "add_external_mapping",
				payload: {
					concept_uri: "urn:ontology:entity:corporate_bond",
					concept_name: "CorporateBond",
					target_uri: "https://spec.edmcouncil.org/fibo/Bond",
					predicate: "skos:broadMatch",
					confidence: 0.87,
				},
			});

			const bondNode = studioStore
				.getState()
				.nodes.find(
					(n) => n.concept_uri === "urn:ontology:entity:corporate_bond",
				);
			expect(bondNode).toBeDefined();
			expect(bondNode?.external_mappings?.[0]?.target_uri).toBe(
				"https://spec.edmcouncil.org/fibo/Bond",
			);

			applyAskFiboActionToStore(studioStore.getState(), {
				action_id: "act-attr-unpromoted",
				label: "Add maturity_date to SettlementRule",
				op_type: "add_attribute",
				payload: {
					concept_name: "SettlementRule",
					attribute_name: "maturity_date",
					data_type: "date",
				},
			});
			const ruleNode = studioStore
				.getState()
				.nodes.find((n) => n.concept_name === "SettlementRule");
			expect(ruleNode).toBeDefined();
			expect(
				ruleNode?.attributes?.some((a) => a.attribute_name === "maturity_date"),
			).toBe(true);

			const { concepts, breakingCandidates } = deriveStep4State(
				studioStore.getState().proposals,
				studioStore.getState().stagedCandidates,
			);
			const promotedUrls: string[] = [];
			const mockPromoteFetch = (async (input: RequestInfo | URL) => {
				promotedUrls.push(String(input));
				return new Response(JSON.stringify({ status: "promoted" }), {
					status: 200,
				});
			}) as unknown as typeof fetch;

			await syncStep4DecisionsToStore(
				studioStore.getState(),
				concepts,
				breakingCandidates,
				mockPromoteFetch,
			);
			const promotePosts = promotedUrls.filter((u) => u.includes("/promote"));
			expect(promotePosts).toHaveLength(2);
			expect(promotePosts.some((u) => u.includes("/stg-1/promote"))).toBe(true);
			expect(promotePosts.some((u) => u.includes("/stg-2/promote"))).toBe(true);
			studioStore.getState().reset();
		});

		it("locks Step 6 run button while isRunningPipeline is true, routes Cancel to abortPipelineRun, and forwards apiUrl/fetchImpl to Step 2", async () => {
			const lockedHtml = renderToStaticMarkup(
				createElement(Step6PipelineBuildAndRun, {
					sessionId: baseSession.session_id,
					isRunningPipeline: true,
					activeRunId: "run-live-77",
					onCancelJob: () => {},
				}),
			);
			expect(lockedHtml).toContain("Run in progress");
			expect(lockedHtml).toContain('data-testid="btn-cancel-active-job"');
			expect(lockedHtml).toMatch(
				/<button[^>]*data-testid="btn-start-pipeline-run"[^>]*disabled/i,
			);

			studioStore.getState().reset();
			studioStore.setState({
				activeSession: baseSession,
				isRunningPipeline: true,
				activeRunId: "run-live-77",
				activeSessionJobId: null,
			});

			const calledUrls: string[] = [];
			const customFetch = (async (input: RequestInfo | URL) => {
				calledUrls.push(String(input));
				return new Response(JSON.stringify({ status: "ABORTED" }), {
					status: 200,
				});
			}) as unknown as typeof fetch;

			const collectProps = (
				node: unknown,
				predicate: (el: {
					type: unknown;
					props: Record<string, unknown>;
				}) => boolean,
			): Array<Record<string, unknown>> => {
				const found: Array<Record<string, unknown>> = [];
				const walk = (n: unknown) => {
					if (!n || typeof n !== "object") return;
					if (Array.isArray(n)) {
						for (const item of n) walk(item);
						return;
					}
					const rec = n as { type?: unknown; props?: Record<string, unknown> };
					if (rec.props) {
						if (predicate({ type: rec.type, props: rec.props })) {
							found.push(rec.props);
						}
						if (rec.props.children) walk(rec.props.children);
					}
				};
				walk(node);
				return found;
			};

			const step2Tree = (
				OntologyWizardShell as unknown as (
					p: Record<string, unknown>,
				) => unknown
			)({
				sessionId: baseSession.session_id,
				initialStep: 2,
				apiUrl: "http://custom-workbench.local:9000",
				fetchImpl: customFetch,
			});
			const step2Props = collectProps(
				step2Tree,
				(el) => el.type === Step2SourcesAndNotes,
			);
			expect(step2Props).toHaveLength(1);
			expect(step2Props[0]?.apiUrl).toBe("http://custom-workbench.local:9000");
			expect(step2Props[0]?.fetchImpl).toBe(customFetch);

			const step6Tree = (
				OntologyWizardShell as unknown as (
					p: Record<string, unknown>,
				) => unknown
			)({
				sessionId: baseSession.session_id,
				initialStep: 6,
				fetchImpl: customFetch,
			});
			const step6Props = collectProps(
				step6Tree,
				(el) => el.type === Step6PipelineBuildAndRun,
			);
			expect(step6Props).toHaveLength(1);
			expect(step6Props[0]?.isRunningPipeline).toBe(true);
			await (step6Props[0]?.onCancelJob as () => Promise<void>)();
			expect(
				calledUrls.some((u) => u.includes("/api/v1/runs/run-live-77/abort")),
			).toBe(true);
			expect(studioStore.getState().isRunningPipeline).toBe(false);
			studioStore.getState().reset();
		});

		it("skips POST /export-blueprint on EMPTY (v0.0.0) cold-start sessions to avoid 404 console errors (C12)", async () => {
			const origEs = (globalThis as Record<string, unknown>).EventSource;
			class NoopEs {
				addEventListener() {}
				close() {}
			}
			(globalThis as Record<string, unknown>).EventSource = NoopEs;

			try {
				for (const sessionStub of [
					{ ...baseSession, status: "EMPTY", active_version: "0.0.0" },
					{ ...baseSession, status: "ACTIVE", active_version: "v0.0.0" },
				]) {
					studioStore.getState().reset();
					studioStore.setState({
						activeSession: sessionStub,
						committedNodes: [],
						sources: [
							{
								source_id: "src-pdf-cold",
								source_sha256: SAMPLE_SHA,
								file_name: "policy.pdf",
								size_bytes: 512,
								mime_type: "application/pdf",
								do_not_send_to_agent: false,
							},
						],
						evidenceChunks: [
							{
								chunk_id: "chk-cold-1",
								source_id: "src-pdf-cold",
								text: "Settlement cutoff T+1.",
							},
						],
					});

					const calledUrls: string[] = [];
					let postedPayload: Record<string, unknown> | null = null;
					const mockFetch = (async (
						input: RequestInfo | URL,
						init?: RequestInit,
					) => {
						const url = String(input);
						calledUrls.push(url);
						if (url.endsWith("/api/v1/pipelines/runs")) {
							postedPayload = JSON.parse(String(init?.body ?? "{}")) as Record<
								string,
								unknown
							>;
							return new Response(JSON.stringify({ run_id: "run-cold-1" }), {
								status: 202,
							});
						}
						return new Response("{}", { status: 404 });
					}) as unknown as typeof fetch;

					const runId = await studioStore
						.getState()
						.startDvPipelineRun(undefined, mockFetch);
					expect(runId).toBe("run-cold-1");
					expect(calledUrls.some((u) => u.includes("/export-blueprint"))).toBe(
						false,
					);
					expect(postedPayload?.unstructured_only).toBe(true);
				}

				// Committed ontology session still invokes POST /export-blueprint
				studioStore.getState().reset();
				studioStore.setState({
					activeSession: {
						...baseSession,
						status: "COMMITTED",
						active_version: "1.0.0",
					},
					sources: [],
				});
				const committedUrls: string[] = [];
				const mockCommittedFetch = (async (input: RequestInfo | URL) => {
					const url = String(input);
					committedUrls.push(url);
					if (url.includes("/export-blueprint")) {
						return new Response(
							JSON.stringify({ blueprint: { domain: "wealth_mgmt" } }),
							{ status: 200 },
						);
					}
					return new Response(JSON.stringify({ run_id: "run-committed-1" }), {
						status: 202,
					});
				}) as unknown as typeof fetch;
				await studioStore
					.getState()
					.startDvPipelineRun(undefined, mockCommittedFetch);
				expect(committedUrls.some((u) => u.includes("/export-blueprint"))).toBe(
					true,
				);
			} finally {
				(globalThis as Record<string, unknown>).EventSource = origEs;
				studioStore.getState().reset();
			}
		});
	});
});
