/**
 * Wave R4-E Pre-Opus Hardening & Regression Suite
 * (apps/opendv-web-workbench/tests/wave-r4e-hardening.test.tsx)
 *
 * Covers:
 * - P14c-MAJ-01: promoteWorkspace contract + ExportPromoteModal remote_platform optional path,
 *   greenfield 64-zeros quick-fill, historical_contract_rationale, and no fake SessionCommit
 * - P14c-MAJ-02: commitChanges hydrates server-verified JCS snapshot via loadGraph without
 *   fabricating client-side FIBO confidence (1.0/0.85) or verified_pack provenance
 * - P14c-MAJ-03: selectSession SSE/poll cleanup, handleJobPayloadUpdate non-terminal stream
 *   preservation, and myGen === activeJobGeneration clobber guards
 * - P14c-MAJ-04: generateLocalExport("json" | "yaml") canonical GraphAOntology conformance
 * - P14c-MAJ-05: Greenfield 0.0.0 waiver submission, QualityFeedbackPanel pending proposal
 *   ungrounded concepts, and ClarificationPanel non-PENDING button gating + Admin override
 * - P14c-MAJ-06 & P14c-MIN-01: Clean startConstruct / acceptProposal payloads and submitAnswer
 *   409/412 draft preservation
 */

import { afterEach, beforeEach, describe, expect, it } from "bun:test";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { ClarificationPanel } from "../src/ontology/components/ClarificationPanel";
import { ExportPromoteModal } from "../src/ontology/components/ExportPromoteModal";
import { QualityFeedbackPanel } from "../src/ontology/components/QualityFeedbackPanel";
import {
	VisualEditorDrawer,
	validateConceptEdit,
} from "../src/ontology/components/VisualEditorDrawer";
import {
	EMPTY_ANSWERS_SHA256,
	canonicalizeGraphADict,
	createStudioStore,
	dumpsJcs,
	generateLocalExport,
	studioStore,
} from "../src/ontology/store/studio-store";
import type {
	ConceptNode,
	RelationshipEdge,
	StudioSessionSummary,
} from "../src/ontology/store/types";

const GENESIS_SHA256 =
	"1916a5c6d501015984b887fd76609b225b72d5d393e5813d8df210b92ab3b4c9";

function makeSession(
	overrides?: Partial<StudioSessionSummary>,
): StudioSessionSummary {
	return {
		session_id: "sess-r4e-1",
		domain_namespace: "private_equity",
		enterprise_owner: "Steward Team",
		status: "COMMITTED",
		active_version: "1.0.0",
		head_sha256: "a".repeat(64),
		created_at: "2026-11-13T00:00:00Z",
		updated_at: "2026-11-13T00:00:00Z",
		...overrides,
	};
}

function makeConcept(overrides?: Partial<ConceptNode>): ConceptNode {
	return {
		id: "urn:opendv:concept:portfolio",
		concept_uri: "urn:opendv:concept:portfolio",
		concept_name: "Portfolio",
		elm_archetype: "CORE_BUSINESS_CONCEPT",
		definition: "Managed investment portfolio.",
		description: "Enterprise portfolio hub.",
		target_hub: "hub_portfolio",
		parent_concept_uri: null,
		confidence: 0.91,
		grounding_status: "GROUNDED",
		attributes: [
			{
				attribute_name: "portfolio_id",
				data_type: "string",
				description: "Business key",
				is_optional: false,
			},
		],
		synonyms: ["Investment Portfolio"],
		external_mappings: [
			{
				scheme: "FIBO",
				target_uri: "https://spec.edmcouncil.org/fibo/ontology/SEC/Portfolio",
				predicate: "skos:exactMatch",
				confidence: 0.91,
				mapping_confidence: 0.91,
				pinned: true,
			},
		],
		...overrides,
	};
}

class MockEventSource {
	static instances: MockEventSource[] = [];
	url: string;
	withCredentials: boolean;
	closed = false;
	onmessage: ((ev: MessageEvent) => void) | null = null;
	onerror: ((ev: Event) => void) | null = null;
	private listeners = new Map<string, Array<(ev: MessageEvent) => void>>();

	constructor(url: string, init?: EventSourceInit) {
		this.url = url;
		this.withCredentials = Boolean(init?.withCredentials);
		MockEventSource.instances.push(this);
	}

	addEventListener(type: string, cb: (ev: MessageEvent) => void) {
		const list = this.listeners.get(type) ?? [];
		list.push(cb);
		this.listeners.set(type, list);
	}

	emit(type: string, payload: unknown) {
		const ev = { data: JSON.stringify(payload) } as MessageEvent;
		if (type === "message" && this.onmessage) {
			this.onmessage(ev);
		}
		for (const cb of this.listeners.get(type) ?? []) {
			cb(ev);
		}
	}

	close() {
		this.closed = true;
	}
}

describe("Wave R4-E: Pre-Opus Hardening Regression Suite", () => {
	const originalEventSource = (globalThis as Record<string, unknown>)
		.EventSource;

	beforeEach(() => {
		MockEventSource.instances = [];
		studioStore.getState().reset();
	});

	afterEach(() => {
		studioStore.getState().reset();
		if (originalEventSource === undefined) {
			(globalThis as Record<string, unknown>).EventSource = undefined;
		} else {
			(globalThis as Record<string, unknown>).EventSource = originalEventSource;
		}
	});

	// =========================================================================
	// 1. P14c-MAJ-01: promoteWorkspace contract & ExportPromoteModal
	// =========================================================================
	describe("1. P14c-MAJ-01: promoteWorkspace contract, remote_platform workspace omission & PROMOTED status", () => {
		it("omits workspace when blank/omitted, sends historical_contract_rationale, sets PROMOTED status, and does not append fake SessionCommit", async () => {
			const headSha = "b".repeat(64);
			const zeroWorkSha = "0".repeat(64);
			const promotedHeadSha = "c".repeat(64);
			const existingCommit = {
				version: "1.0.0",
				sha256: headSha,
				parent_sha256: GENESIS_SHA256,
				rationale: "Initial ontology commit",
				timestamp: "2026-11-13T00:00:00Z",
			};

			const store = createStudioStore({
				activeMode: "remote_platform",
				initialSession: makeSession({
					session_id: "sess-promote-remote",
					head_sha256: headSha,
					active_version: "1.0.0",
					status: "COMMITTED",
					commits: [existingCommit],
				}),
			});
			store.setState({
				sessions: [
					makeSession({
						session_id: "sess-promote-remote",
						head_sha256: headSha,
						active_version: "1.0.0",
						status: "COMMITTED",
						commits: [existingCommit],
					}),
				],
			});

			let capturedBody: Record<string, unknown> | null = null;
			const mockFetch: typeof fetch = (async (_url, init) => {
				capturedBody = JSON.parse(String(init?.body || "{}")) as Record<
					string,
					unknown
				>;
				return new Response(
					JSON.stringify({
						promoted_version: "1.0.0",
						workspace_sha256: "d".repeat(64),
						head_sha256: promotedHeadSha,
					}),
					{ status: 200 },
				);
			}) as typeof fetch;

			const ok = await store.getState().promoteWorkspace(
				{
					workspace: "   ",
					workspace_sha256: zeroWorkSha,
					head_sha256: headSha,
					rationale: "Promote greenfield remote ontology to pipeline",
					acknowledge_historical_contract_warning: true,
					historical_contract_rationale:
						"Approved intentional historical contract update by Chief Architect",
				},
				mockFetch,
			);

			expect(ok).toBe(true);
			expect(capturedBody).not.toBeNull();
			expect("workspace" in (capturedBody ?? {})).toBe(false);
			expect(capturedBody?.historical_contract_rationale).toBe(
				"Approved intentional historical contract update by Chief Architect",
			);
			expect(capturedBody?.acknowledge_historical_contract_warning).toBe(true);

			// Commits list is untouched (no fake SessionCommit appended!)
			expect(store.getState().activeSession?.commits).toEqual([existingCommit]);
			expect(store.getState().activeSession?.status).toBe("PROMOTED");
			expect(store.getState().sessions[0]?.status).toBe("PROMOTED");
		});

		it("renders remote_platform optional empty workspacePath and greenfield 64-zeros quick-fill helper in ExportPromoteModal", () => {
			studioStore.setState({
				activeMode: "remote_platform",
				activeSession: makeSession({
					domain_namespace: "private_equity",
					head_sha256: "a".repeat(64),
				}),
			});

			const html = renderToStaticMarkup(
				createElement(ExportPromoteModal, {
					isOpen: true,
					initialTab: "promote",
					onClose: () => {},
				}),
			);

			expect(html).toContain(
				"Target Workspace Path (Optional — Server Managed):",
			);
			expect(html).not.toContain('value="workspaces/private_equity"');
			expect(html).toContain('data-testid="greenfield-workspace-sha-btn"');
			expect(html).toContain("Greenfield (64 zeros)");
		});
	});

	// =========================================================================
	// 2. P14c-MAJ-02: commitChanges server-verified JCS snapshot reload
	// =========================================================================
	describe("2. P14c-MAJ-02: commitChanges reloads server-verified ontology snapshot without fabricating confidence", () => {
		it("hydrates true server-verified confidence/provenance via loadGraph after POST /delta and avoids fabricating 1.0/0.85 on fallback", async () => {
			const session = makeSession({
				active_version: "0.0.0",
				head_sha256: GENESIS_SHA256,
				status: "EMPTY",
			});
			const store = createStudioStore({ initialSession: session });

			const userAssertedNode = makeConcept({
				confidence: undefined,
				grounding_status: "GROUNDED",
				external_mappings: [
					{
						scheme: "FIBO",
						target_uri:
							"https://spec.edmcouncil.org/fibo/ontology/SEC/Portfolio",
						predicate: "skos:exactMatch",
						pinned: true,
						provenance: "user_asserted",
					},
				],
			});
			store.getState().addConcept(userAssertedNode);

			const nextSha = "f".repeat(64);
			let exportCalledWithVersion = "";
			const mockFetchWithExport: typeof fetch = (async (url, init) => {
				const u = String(url);
				const method = (init?.method || "GET").toUpperCase();
				if (method === "POST" && u.endsWith("/delta")) {
					return new Response(
						JSON.stringify({
							target_version: "1.0.0",
							target_sha256: nextSha,
						}),
						{ status: 200 },
					);
				}
				if (method === "GET" && u.includes("/export?")) {
					exportCalledWithVersion = u;
					return new Response(
						JSON.stringify({
							ontology_version: "1.0.0",
							domain_namespace: "private_equity",
							enterprise_owner: "Steward Team",
							canonical_business_concepts: [
								{
									concept_uri: "urn:opendv:concept:portfolio",
									concept_name: "Portfolio",
									elm_archetype: "CORE_BUSINESS_CONCEPT",
									definition: "Managed investment portfolio.",
									target_hub: "hub_portfolio",
									external_mappings: [
										{
											scheme: "FIBO",
											target_uri:
												"https://spec.edmcouncil.org/fibo/ontology/SEC/Portfolio",
											predicate: "skos:exactMatch",
											pinned: true,
											mapping_confidence: 0.78,
											provenance: "verified_pack",
											reference_pack_sha256: "9".repeat(64),
										},
									],
								},
							],
							canonical_relationships: [],
						}),
						{ status: 200 },
					);
				}
				return new Response("{}", { status: 404 });
			}) as typeof fetch;

			const ok = await store
				.getState()
				.commitChanges("Commit genesis portfolio", mockFetchWithExport);
			expect(ok).toBe(true);
			expect(exportCalledWithVersion).toContain("version=1.0.0");
			expect(store.getState().activeSession?.status).toBe("COMMITTED");

			const reloadedMapping = store.getState().nodes[0]?.external_mappings?.[0];
			expect(reloadedMapping?.mapping_confidence).toBe(0.78);
			expect(reloadedMapping?.provenance).toBe("verified_pack");
			expect(
				store.getState().committedNodes[0]?.external_mappings?.[0]
					?.mapping_confidence,
			).toBe(0.78);

			// Fallback when GET /export returns HTTP 500: must preserve error toast AND strip user_asserted confidence / GROUNDED status
			const fallbackStore = createStudioStore({ initialSession: session });
			fallbackStore.getState().addConcept({
				...userAssertedNode,
				confidence: 0.95,
				grounding_status: "GROUNDED",
				external_mappings: [
					{
						scheme: "FIBO",
						target_uri:
							"https://spec.edmcouncil.org/fibo/ontology/SEC/Portfolio",
						predicate: "skos:exactMatch",
						pinned: true,
						confidence: 0.95,
						mapping_confidence: 0.95,
						provenance: "user_asserted",
					},
				],
			});
			const mockFailingExportFetch: typeof fetch = (async (url, init) => {
				const u = String(url);
				const method = (init?.method || "GET").toUpperCase();
				if (method === "POST" && u.endsWith("/delta")) {
					return new Response(
						JSON.stringify({
							target_version: "1.0.0",
							target_sha256: nextSha,
						}),
						{ status: 200 },
					);
				}
				return new Response(
					JSON.stringify({
						detail: "Snapshot read failure",
						code: "GRAPH_LOAD_FAILED",
					}),
					{ status: 500 },
				);
			}) as typeof fetch;

			const fallbackOk = await fallbackStore
				.getState()
				.commitChanges("Commit fallback test", mockFailingExportFetch);
			expect(fallbackOk).toBe(true);
			const fallbackNode = fallbackStore.getState().nodes[0];
			const fallbackMapping = fallbackNode?.external_mappings?.[0];
			expect(fallbackMapping?.mapping_confidence).toBeUndefined();
			expect(fallbackMapping?.confidence).toBeUndefined();
			expect(fallbackMapping?.provenance).toBe("user_asserted");
			expect(fallbackNode?.grounding_status).toBe("UNGROUNDED");
			expect(
				fallbackStore
					.getState()
					.toasts.some(
						(t) => t.status === 500 && t.code === "GRAPH_LOAD_FAILED",
					),
			).toBe(true);
		});
	});

	// =========================================================================
	// 3. P14c-MAJ-03: selectSession SSE/timer cleanup & stale watcher guards
	// =========================================================================
	describe("3. P14c-MAJ-03: selectSession SSE/timer cleanup, non-terminal SSE preservation & stale generation guards", () => {
		it("closes EventSources on session switch and prevents stale poll errors from clobbering a newly launched job", async () => {
			(globalThis as Record<string, unknown>).EventSource = MockEventSource;
			const sessA = makeSession({ session_id: "sess-a" });
			const sessB = makeSession({ session_id: "sess-b" });

			const store = createStudioStore({
				initialSession: sessA,
				initialPollIntervalMs: 15,
				maxPollErrorRetries: 0,
			});
			store.setState({ sessions: [sessA, sessB] });

			const mockSseFetch: typeof fetch = (async (url, init) => {
				const u = String(url);
				const method = (init?.method || "GET").toUpperCase();
				if (method === "POST" && u.endsWith("/construct")) {
					return new Response(
						JSON.stringify({ job_id: "job-sess-a-1", status: "RUNNING" }),
						{ status: 202 },
					);
				}
				if (u.includes("/sessions/sess-b")) {
					return new Response(JSON.stringify(sessB), { status: 200 });
				}
				return new Response("{}", { status: 200 });
			}) as typeof fetch;

			await store.getState().startConstruct({}, mockSseFetch);
			expect(MockEventSource.instances.length).toBe(1);
			expect(MockEventSource.instances[0]?.closed).toBe(false);

			// Non-terminal SSE event while activeSession temporarily differs must NOT close EventSource
			store.setState({ activeSession: sessB });
			MockEventSource.instances[0]?.emit("progress", {
				status: "RUNNING",
				stage: "indexing",
				progress_pct: 40,
			});
			await new Promise((r) => setTimeout(r, 10));
			expect(MockEventSource.instances[0]?.closed).toBe(false);

			// Restore sessA and explicitly selectSession("sess-b"): must close active EventSources & increment generation
			store.setState({ activeSession: sessA });
			await store.getState().selectSession("sess-b", mockSseFetch);
			expect(MockEventSource.instances[0]?.closed).toBe(true);
			expect(store.getState().isConstructing).toBe(false);

			// Now verify stale polling watcher in-flight HTTP error cannot clobber a newly launched job on the same session
			(globalThis as Record<string, unknown>).EventSource = undefined;
			let resolveJob1Poll: ((res: Response) => void) | null = null;

			const mockRaceFetch: typeof fetch = (async (url, init) => {
				const u = String(url);
				const method = (init?.method || "GET").toUpperCase();
				if (method === "POST" && u.endsWith("/clarify")) {
					return new Response(
						JSON.stringify({
							job_id: resolveJob1Poll ? "job-clarify-2" : "job-clarify-1",
							status: "RUNNING",
						}),
						{ status: 202 },
					);
				}
				if (method === "GET" && u.endsWith("/jobs/job-clarify-1")) {
					return await new Promise<Response>((resolve) => {
						resolveJob1Poll = resolve;
					});
				}
				if (method === "GET" && u.endsWith("/jobs/job-clarify-2")) {
					return new Response(
						JSON.stringify({
							job_id: "job-clarify-2",
							status: "RUNNING",
							progress_pct: 50,
						}),
						{ status: 200 },
					);
				}
				return new Response("{}", { status: 200 });
			}) as typeof fetch;

			await store.getState().startClarify({}, mockRaceFetch);
			await new Promise((r) => setTimeout(r, 30));
			expect(resolveJob1Poll).not.toBeNull();

			// Launch second job before job-clarify-1's in-flight poll fails
			const job2 = await store.getState().startClarify({}, mockRaceFetch);
			expect(job2).toBe("job-clarify-2");
			expect(store.getState().isClarifying).toBe(true);

			// Resolve stale job-1 poll with HTTP 500 -> must NOT clobber isClarifying!
			const staleResolver = resolveJob1Poll as ((res: Response) => void) | null;
			staleResolver?.(
				new Response(JSON.stringify({ detail: "Stale worker crash" }), {
					status: 500,
				}),
			);
			await new Promise((r) => setTimeout(r, 25));
			expect(store.getState().isClarifying).toBe(true);
		});
	});

	// =========================================================================
	// 4. P14c-MAJ-04: generateLocalExport("json" | "yaml") GraphAOntology conformance
	// =========================================================================
	describe("4. P14c-MAJ-04: generateLocalExport('json' | 'yaml') canonical GraphAOntology conformance", () => {
		it("emits canonical_business_concepts, canonical_relationships, target_hub, target_link, endpoints, and exact JCS JSON", () => {
			const c1 = makeConcept();
			const c2 = makeConcept({
				id: "urn:opendv:concept:asset",
				concept_uri: "urn:opendv:concept:asset",
				concept_name: "Asset",
				target_hub: "hub_asset",
			});
			const edge: RelationshipEdge = {
				id: "urn:opendv:relationship:holds_asset",
				relationship_uri: "urn:opendv:relationship:holds_asset",
				relationship_name: "Holds Asset",
				target_link: "lnk_portfolio_asset",
				source_concept_uri: "urn:opendv:concept:portfolio",
				target_concept_uri: "urn:opendv:concept:asset",
				endpoints: ["urn:opendv:concept:portfolio", "urn:opendv:concept:asset"],
			};
			const sess = makeSession({
				domain_namespace: "private_equity",
				enterprise_owner: "Data Architecture",
				active_version: "1.2.0",
			});

			const jsonExport = generateLocalExport(
				"json",
				"1.2.0",
				[c1, c2],
				[edge],
				sess,
			);
			const expectedCanonical = canonicalizeGraphADict([c1, c2], [edge], {
				...sess,
				active_version: "1.2.0",
			});
			expect(jsonExport).toBe(
				dumpsJcs(expectedCanonical, { excludeNone: true }),
			);

			const parsedJson = JSON.parse(jsonExport) as Record<string, unknown>;
			expect(parsedJson.ontology_version).toBe("1.2.0");
			expect(parsedJson.domain_namespace).toBe("private_equity");
			expect(parsedJson.enterprise_owner).toBe("Data Architecture");
			expect(Array.isArray(parsedJson.canonical_business_concepts)).toBe(true);
			expect(Array.isArray(parsedJson.canonical_relationships)).toBe(true);
			expect("concepts" in parsedJson).toBe(false);
			expect("relationships" in parsedJson).toBe(false);

			const yamlExport = generateLocalExport(
				"yaml",
				"1.2.0",
				[c1, c2],
				[edge],
				sess,
			);
			expect(yamlExport).toContain('ontology_version: "1.2.0"');
			expect(yamlExport).toContain('domain_namespace: "private_equity"');
			expect(yamlExport).toContain('enterprise_owner: "Data Architecture"');
			expect(yamlExport).toContain("canonical_business_concepts:");
			expect(yamlExport).toContain('target_hub: "hub_portfolio"');
			expect(yamlExport).toContain("canonical_relationships:");
			expect(yamlExport).toContain('target_link: "lnk_portfolio_asset"');
			expect(yamlExport).toContain("endpoints:");
		});
	});

	// =========================================================================
	// 5. P14c-MAJ-05: Greenfield 0.0.0 waiver & proposal ratification fixes
	// =========================================================================
	describe("5. P14c-MAJ-05: Greenfield 0.0.0 waiver authorization & proposal ratification gating", () => {
		it("authorizes waiver on 0.0.0 greenfield session using pending proposal add_concept URIs and renders them in QualityFeedbackPanel", async () => {
			const greenfieldSession = makeSession({
				session_id: "sess-greenfield-0",
				active_version: "0.0.0",
				head_sha256: GENESIS_SHA256,
				status: "CONSTRUCTED",
			});
			const pendingProposals = [
				{
					proposal_id: "prop-green-1",
					status: "PENDING",
					base_sha256: GENESIS_SHA256,
					target_version: "1.0.0",
					ops_count: 1,
					created_at: "2026-11-13T00:00:00Z",
					operations: [
						{
							op_id: "op-1",
							op_type: "add_concept",
							concept_uri: "urn:opendv:concept:waterfall_tier",
							concept_name: "Waterfall Tier",
							external_mappings: [],
						},
					],
				},
			];

			const store = createStudioStore({
				initialSession: greenfieldSession,
				userRole: "admin",
			});
			store.setState({
				nodes: [],
				edges: [],
				proposals: pendingProposals,
			});

			let capturedWaiverBody: Record<string, unknown> | null = null;
			const mockWaiverFetch: typeof fetch = (async (_url, init) => {
				capturedWaiverBody = JSON.parse(String(init?.body || "{}")) as Record<
					string,
					unknown
				>;
				return new Response(
					JSON.stringify({
						waiver_applied: true,
						waived_concept_uris: ["urn:opendv:concept:waterfall_tier"],
						waived_base_sha256: GENESIS_SHA256,
					}),
					{ status: 200 },
				);
			}) as typeof fetch;

			const ok = await store
				.getState()
				.submitWaiver(
					"Proprietary waterfall carry concept has no FIBO equivalent",
					"admin-waiver-secret-key",
					mockWaiverFetch,
				);
			expect(ok).toBe(true);
			expect(capturedWaiverBody?.target_concept_uris).toEqual([
				"urn:opendv:concept:waterfall_tier",
			]);

			// QualityFeedbackPanel surfaces the ungrounded concept from the PENDING proposal even when nodes === []
			studioStore.setState({
				activeSession: greenfieldSession,
				nodes: [],
				proposals: pendingProposals,
				qualityScorecard: {
					...studioStore.getState().qualityScorecard,
					waiver_applied: true,
					waiver_rationale:
						"Proprietary waterfall carry concept has no FIBO equivalent",
					waived_concept_uris: ["urn:opendv:concept:waterfall_tier"],
				},
			});
			const qfpHtml = renderToStaticMarkup(createElement(QualityFeedbackPanel));
			expect(qfpHtml).toContain(
				'data-testid="waiver-concept-checkbox-urn:opendv:concept:waterfall_tier"',
			);
			expect(qfpHtml).toContain("Waterfall Tier");
		});

		it("disables Accept and Rebase for non-PENDING proposals and renders rationale + Admin self-acceptance override on PENDING proposals", () => {
			studioStore.setState({
				activeSession: makeSession(),
				userRole: "admin",
				proposals: [
					{
						proposal_id: "prop-pending",
						status: "PENDING",
						base_sha256: "a".repeat(64),
						target_version: "1.1.0",
						ops_count: 1,
						created_at: "2026-11-13T00:00:00Z",
					},
					{
						proposal_id: "prop-superseded",
						status: "SUPERSEDED",
						base_sha256: "a".repeat(64),
						target_version: "1.1.0",
						ops_count: 1,
						created_at: "2026-11-13T00:00:00Z",
					},
					{
						proposal_id: "prop-rejected",
						status: "REJECTED",
						base_sha256: "a".repeat(64),
						target_version: "1.1.0",
						ops_count: 1,
						created_at: "2026-11-13T00:00:00Z",
					},
				],
			});

			const html = renderToStaticMarkup(createElement(ClarificationPanel));

			// PENDING proposal has enabled Accept/Rebase + rationale input + Admin override checkbox
			expect(html).toContain(
				'data-testid="proposal-accept-rationale-prop-pending"',
			);
			expect(html).toContain(
				'data-testid="proposal-self-accept-override-prop-pending"',
			);
			expect(html).not.toMatch(
				/<button(?=[^>]*\bdisabled="")(?=[^>]*data-testid="accept-proposal-btn-prop-pending")[^>]*>/,
			);
			expect(html).not.toMatch(
				/<button(?=[^>]*\bdisabled="")(?=[^>]*data-testid="rebase-proposal-btn-prop-pending")[^>]*>/,
			);

			// SUPERSEDED and REJECTED proposals have disabled Accept & Rebase buttons
			for (const pid of ["prop-superseded", "prop-rejected"]) {
				expect(html).toMatch(
					new RegExp(
						`<button(?=[^>]*\\bdisabled="")(?=[^>]*data-testid="accept-proposal-btn-${pid}")[^>]*>`,
					),
				);
				expect(html).toMatch(
					new RegExp(
						`<button(?=[^>]*\\bdisabled="")(?=[^>]*data-testid="rebase-proposal-btn-${pid}")[^>]*>`,
					),
				);
			}
		});
	});

	// =========================================================================
	// 6. P14c-MAJ-06 & P14c-MIN-01: Payload hygiene & submitAnswer 409/412 draft preservation
	// =========================================================================
	describe("6. P14c-MAJ-06 & P14c-MIN-01: Clean startConstruct / acceptProposal payloads & submitAnswer 409/412 draft preservation", () => {
		it("omits undocumented rationale from startConstruct and omits duplicate rationale from acceptProposal", async () => {
			(globalThis as Record<string, unknown>).EventSource = undefined;
			const sess = makeSession({ head_sha256: "e".repeat(64) });
			const store = createStudioStore({
				initialSession: sess,
				userRole: "admin",
			});

			let constructBody: Record<string, unknown> | null = null;
			let acceptBody: Record<string, unknown> | null = null;

			const mockFetch: typeof fetch = (async (url, init) => {
				const u = String(url);
				const method = (init?.method || "GET").toUpperCase();
				if (method === "POST" && u.endsWith("/construct")) {
					constructBody = JSON.parse(String(init?.body || "{}")) as Record<
						string,
						unknown
					>;
					return new Response(
						JSON.stringify({ job_id: "job-c-1", status: "COMPLETED" }),
						{ status: 200 },
					);
				}
				if (method === "POST" && u.endsWith("/proposals/prop-1/accept")) {
					acceptBody = JSON.parse(String(init?.body || "{}")) as Record<
						string,
						unknown
					>;
					return new Response(JSON.stringify({ status: "ACCEPTED" }), {
						status: 200,
					});
				}
				return new Response(JSON.stringify(sess), { status: 200 });
			}) as typeof fetch;

			await store
				.getState()
				.startConstruct(
					{ mode: "deterministic", auto_commit: true, rationale: "Ignored" },
					mockFetch,
				);
			expect(constructBody).not.toBeNull();
			expect("rationale" in (constructBody ?? {})).toBe(false);
			expect(constructBody?.auto_commit).toBe(true);

			await store
				.getState()
				.acceptProposal("prop-1", "Steward ratification rationale", mockFetch, {
					selfAcceptanceOverride: true,
				});
			expect(acceptBody).not.toBeNull();
			expect(acceptBody?.grounding_rationale).toBe(
				"Steward ratification rationale",
			);
			expect(acceptBody?.self_acceptance_override).toBe(true);
			expect("rationale" in (acceptBody ?? {})).toBe(false);
		});

		it("preserves unsaved local answer drafts over stale server answers when submitAnswer receives HTTP 409/412", async () => {
			const sess = makeSession({ session_id: "sess-occ-drafts" });
			const store = createStudioStore({ initialSession: sess });

			store.setState({
				answers_sha256: EMPTY_ANSWERS_SHA256,
				clarifications: [
					{
						question_id: "q1",
						concept_name: "Fund",
						question_text: "Fund grain?",
						certainty: "HIGH",
						rationale_options: [{ id: "opt-a", label: "Option A" }],
						evidence_citations: [],
						status: "PENDING",
					},
					{
						question_id: "q2",
						concept_name: "Deal",
						question_text: "Deal grain?",
						certainty: "MEDIUM",
						rationale_options: [{ id: "opt-b", label: "Option B" }],
						evidence_citations: [],
						status: "PENDING",
					},
				],
			});

			// User prepares local drafts for both q1 and q2
			store.getState().setAnswerDraft("q1", ["opt-a"], "Local draft for q1");
			store
				.getState()
				.setAnswerDraft("q2", ["opt-local-new"], "Unsent local draft for q2");

			let reloadedQuestionsUrl = "";
			const mockOccFetch: typeof fetch = (async (url, init) => {
				const u = String(url);
				const method = (init?.method || "GET").toUpperCase();
				if (method === "POST" && u.endsWith("/answers")) {
					return new Response(
						JSON.stringify({
							detail: "Concurrent answer update",
							code: "OCC_CONFLICT",
						}),
						{ status: 412 },
					);
				}
				if (method === "GET" && u.endsWith("/questions")) {
					reloadedQuestionsUrl = u;
					return new Response(
						JSON.stringify({
							questions: [
								{
									question_id: "q1",
									concept_name: "Fund",
									question_text: "Fund grain?",
									options: [{ id: "opt-a", label: "Option A" }],
								},
								{
									question_id: "q2",
									concept_name: "Deal",
									question_text: "Deal grain?",
									options: [{ id: "opt-b", label: "Option B" }],
								},
								{
									question_id: "q3",
									concept_name: "LP",
									question_text: "LP grain?",
									options: [{ id: "opt-c", label: "Option C" }],
								},
							],
						}),
						{ status: 200 },
					);
				}
				if (method === "GET" && u.endsWith("/answers")) {
					return new Response(
						JSON.stringify({
							answers_sha256: "9".repeat(64),
							answers: [
								{
									question_id: "q2",
									selected_options: ["opt-stale-server"],
									user_text: "Stale server answer for q2",
								},
								{
									question_id: "q3",
									selected_options: ["opt-c"],
									user_text: "Server answer for q3",
								},
							],
						}),
						{ status: 200 },
					);
				}
				return new Response("{}", { status: 200 });
			}) as typeof fetch;

			const ok = await store.getState().submitAnswer(
				{
					question_id: "q1",
					selected_option_ids: ["opt-a"],
					custom_answer_text: "Local draft for q1",
				},
				EMPTY_ANSWERS_SHA256,
				mockOccFetch,
			);

			expect(ok).toBe(false);
			expect(reloadedQuestionsUrl).toContain(
				"/sessions/sess-occ-drafts/questions",
			);
			// Server's new answers_sha256 and q3 answer are hydrated, while local drafts for q1 & q2 win over stale server q2!
			expect(store.getState().answers_sha256).toBe("9".repeat(64));
			expect(store.getState().answers.q3?.selected_option_ids).toEqual([
				"opt-c",
			]);
			expect(store.getState().answers.q1?.custom_answer_text).toBe(
				"Local draft for q1",
			);
			expect(store.getState().answers.q2?.selected_option_ids).toEqual([
				"opt-local-new",
			]);
			expect(store.getState().answers.q2?.custom_answer_text).toBe(
				"Unsent local draft for q2",
			);
		});
	});

	// =========================================================================
	// 7. Opus Remediation Regression Suite (P14c2::MAJ-01, P14c3::MAJ-01, P14c3::MAJ-02, P14c4::MAJ-01, P14c4::MAJ-02)
	// =========================================================================
	describe("7. Opus Remediation Regression Suite: startConstruct auto_commit guards, historical diff, n-ary relationship diffing & post-commit loadGraph safety", () => {
		it("P14c2::MAJ-01: blocks startConstruct({ auto_commit: true }) when hasUnsavedChanges is true unless force: true", async () => {
			(globalThis as Record<string, unknown>).EventSource = undefined;
			const sess = makeSession({ head_sha256: "a".repeat(64) });
			const store = createStudioStore({ initialSession: sess });
			store.getState().addConcept(makeConcept());
			expect(store.getState().hasUnsavedChanges).toBe(true);

			let constructCalled = false;
			const mockFetch: typeof fetch = (async (url, init) => {
				const u = String(url);
				const method = (init?.method || "GET").toUpperCase();
				if (method === "POST" && u.endsWith("/construct")) {
					constructCalled = true;
					return new Response(
						JSON.stringify({ job_id: "job-forced-1", status: "QUEUED" }),
						{ status: 202 },
					);
				}
				return new Response(JSON.stringify({ status: "COMPLETED" }), {
					status: 200,
				});
			}) as typeof fetch;

			// Blocked without force: true
			const blockedJobId = await store
				.getState()
				.startConstruct({ auto_commit: true }, mockFetch);
			expect(blockedJobId).toBeNull();
			expect(constructCalled).toBe(false);
			const warnToast = store.getState().toasts[0];
			expect(warnToast?.title).toBe("Unsaved Canvas Edits");
			expect(warnToast?.code).toBe("UNSAVED_CHANGES");
			expect(warnToast?.status).toBe(409);

			// Non-auto_commit (proposal mode) is allowed even when canvas is dirty
			const proposalJobId = await store
				.getState()
				.startConstruct({ auto_commit: false }, mockFetch);
			expect(proposalJobId).toBe("job-forced-1");
			expect(constructCalled).toBe(true);

			// Explicit force: true overrides guard for auto_commit
			constructCalled = false;
			const forcedJobId = await store
				.getState()
				.startConstruct({ auto_commit: true, force: true }, mockFetch);
			expect(forcedJobId).toBe("job-forced-1");
			expect(constructCalled).toBe(true);
			store.getState().reset();
		});

		it("P14c3::MAJ-01: preserves dirty canvas and pins baseline ETag when auto-commit construct completes, while reloading cleanly when canvas is clean", async () => {
			(globalThis as Record<string, unknown>).EventSource = MockEventSource;
			MockEventSource.instances = [];

			const baselineSha = "1".repeat(64);
			const serverAdvancedSha = "2".repeat(64);
			const sess = makeSession({
				session_id: "sess-autocommit-dirty",
				active_version: "1.0.0",
				head_sha256: baselineSha,
			});
			const baseNode = makeConcept();
			const store = createStudioStore({ initialSession: sess });
			store.setState({
				sessions: [sess],
				nodes: [baseNode],
				committedNodes: [baseNode],
				hasUnsavedChanges: false,
			});

			let selectSessionDetailCalls = 0;
			let loadSessionsCalls = 0;

			const mockFetch: typeof fetch = (async (url, init) => {
				const u = String(url);
				const method = (init?.method || "GET").toUpperCase();
				if (method === "POST" && u.endsWith("/construct")) {
					return new Response(
						JSON.stringify({ job_id: "job-ac-sse", status: "RUNNING" }),
						{ status: 202 },
					);
				}
				if (method === "GET" && u.endsWith("/ontology/studio/sessions")) {
					loadSessionsCalls += 1;
					return new Response(
						JSON.stringify({
							sessions: [
								{
									...sess,
									active_version: "1.1.0",
									head_sha256: serverAdvancedSha,
								},
							],
						}),
						{ status: 200 },
					);
				}
				if (
					method === "GET" &&
					u.endsWith(`/sessions/${encodeURIComponent(sess.session_id)}`)
				) {
					selectSessionDetailCalls += 1;
					return new Response(
						JSON.stringify({
							...sess,
							active_version: "1.1.0",
							head_sha256: serverAdvancedSha,
						}),
						{ status: 200 },
					);
				}
				if (method === "GET" && u.includes("/export?")) {
					return new Response(
						JSON.stringify(
							canonicalizeGraphADict(
								[
									baseNode,
									makeConcept({
										id: "urn:opendv:concept:server_fund",
										concept_uri: "urn:opendv:concept:server_fund",
										concept_name: "Server Fund",
									}),
								],
								[],
								{ ...sess, active_version: "1.1.0" },
							),
						),
						{ status: 200 },
					);
				}
				return new Response(JSON.stringify([]), { status: 200 });
			}) as typeof fetch;

			const jobId = await store
				.getState()
				.startConstruct({ auto_commit: true }, mockFetch);
			expect(jobId).toBe("job-ac-sse");
			expect(MockEventSource.instances.length).toBe(1);

			// Steward edits canvas while background auto-commit construction is running
			const localDraftNode = makeConcept({
				id: "urn:opendv:concept:local_draft_deal",
				concept_uri: "urn:opendv:concept:local_draft_deal",
				concept_name: "Local Draft Deal",
			});
			store.getState().addConcept(localDraftNode);
			expect(store.getState().hasUnsavedChanges).toBe(true);

			// Background job finishes with auto_committed: true
			const es = MockEventSource.instances[0];
			es?.emit("job_completed", {
				job_id: "job-ac-sse",
				status: "COMPLETED",
				result: { auto_committed: true, target_version: "1.1.0" },
			});
			await new Promise((r) => setTimeout(r, 25));

			// Dirty canvas MUST NOT be wiped by selectSession(..., true)!
			expect(selectSessionDetailCalls).toBe(0);
			expect(loadSessionsCalls).toBe(1);
			expect(store.getState().hasUnsavedChanges).toBe(true);
			expect(store.getState().nodes.map((n) => n.concept_uri)).toEqual([
				"urn:opendv:concept:portfolio",
				"urn:opendv:concept:local_draft_deal",
			]);
			expect(store.getState().committedNodes.map((n) => n.concept_uri)).toEqual(
				["urn:opendv:concept:portfolio"],
			);
			// Sessions list reflects 1.1.0 on server, while activeSession stays pinned to baseline 1.0.0 / baselineSha for OCC
			expect(store.getState().sessions[0]?.active_version).toBe("1.1.0");
			expect(store.getState().activeSession?.active_version).toBe("1.0.0");
			expect(store.getState().activeSession?.head_sha256).toBe(baselineSha);
			expect(
				store
					.getState()
					.toasts.some(
						(t) =>
							t.title === "New Version Committed on Server" &&
							t.code === "SERVER_HEAD_ADVANCED" &&
							t.status === 409,
					),
			).toBe(true);

			// Clean canvas case: selectSession IS invoked and loads v1.1.0
			const cleanStore = createStudioStore({ initialSession: sess });
			cleanStore.setState({
				sessions: [sess],
				nodes: [baseNode],
				committedNodes: [baseNode],
				hasUnsavedChanges: false,
			});
			MockEventSource.instances = [];
			await cleanStore
				.getState()
				.startConstruct({ auto_commit: true }, mockFetch);
			MockEventSource.instances[0]?.emit("job_completed", {
				job_id: "job-ac-sse",
				status: "COMPLETED",
				result: { auto_committed: true, target_version: "1.1.0" },
			});
			await new Promise((r) => setTimeout(r, 25));
			expect(selectSessionDetailCalls).toBe(1);
			expect(cleanStore.getState().activeSession?.active_version).toBe("1.1.0");
			expect(cleanStore.getState().nodes.length).toBe(2);
		});

		it("P14c3::MAJ-02: diffs historical v1.0.0 against HEAD v1.2.0 accurately when isHistoricalView is true (online & offline)", async () => {
			const sess = makeSession({
				session_id: "sess-hist-diff",
				active_version: "1.2.0",
				head_sha256: "c".repeat(64),
			});
			const v1Portfolio = makeConcept();
			const v12Fund = makeConcept({
				id: "urn:opendv:concept:fund_v12",
				concept_uri: "urn:opendv:concept:fund_v12",
				concept_name: "Fund V12",
			});

			const store = createStudioStore({ initialSession: sess });
			store.setState({
				nodes: [v1Portfolio, v12Fund],
				committedNodes: [v1Portfolio, v12Fund],
				edges: [],
				committedEdges: [],
				viewedVersion: "1.2.0",
				isHistoricalView: false,
			});

			const exportUrls: string[] = [];
			const mockHistFetch: typeof fetch = (async (url) => {
				const u = String(url);
				exportUrls.push(u);
				if (u.includes("version=1.0.0")) {
					return new Response(
						JSON.stringify(
							canonicalizeGraphADict([v1Portfolio], [], {
								...sess,
								active_version: "1.0.0",
							}),
						),
						{ status: 200 },
					);
				}
				if (u.includes("version=1.2.0")) {
					return new Response(
						JSON.stringify(
							canonicalizeGraphADict([v1Portfolio, v12Fund], [], {
								...sess,
								active_version: "1.2.0",
							}),
						),
						{ status: 200 },
					);
				}
				return new Response("{}", { status: 404 });
			}) as typeof fetch;

			// Open historical version 1.0.0 -> isHistoricalView === true, nodes holds [v1Portfolio], committedNodes holds [v1Portfolio, v12Fund]
			await store.getState().loadGraph("1.0.0", mockHistFetch);
			expect(store.getState().isHistoricalView).toBe(true);
			expect(store.getState().viewedVersion).toBe("1.0.0");
			expect(store.getState().nodes.length).toBe(1);
			expect(store.getState().committedNodes.length).toBe(2);

			// 1. Synchronous computeDiff("1.0.0", "1.2.0") uses nodes (1.0.0) vs committedNodes (1.2.0) — NOT nodes vs nodes!
			store.getState().computeDiff("1.0.0", "1.2.0");
			expect(store.getState().diffState.summary.added_count).toBe(1);
			expect(store.getState().diffState.records[0]?.identifier).toBe(
				"urn:opendv:concept:fund_v12",
			);

			// 2. Online loadAndComputeDiff("1.0.0", "1.2.0") fetches version=1.2.0 instead of fast-pathing to historical get().nodes
			exportUrls.length = 0;
			await store
				.getState()
				.loadAndComputeDiff("1.0.0", "1.2.0", mockHistFetch);
			expect(exportUrls.some((u) => u.includes("version=1.2.0"))).toBe(true);
			expect(store.getState().diffState.summary.added_count).toBe(1);
			expect(store.getState().diffState.records[0]?.identifier).toBe(
				"urn:opendv:concept:fund_v12",
			);

			// 3. Offline loadAndComputeDiff("1.0.0", "1.2.0") falls back to historical canvas (1.0.0) vs committedNodes (1.2.0)
			const mockOfflineFetch: typeof fetch = (async () => {
				throw new Error("Offline");
			}) as typeof fetch;
			await store
				.getState()
				.loadAndComputeDiff("1.0.0", "1.2.0", mockOfflineFetch);
			expect(store.getState().diffState.summary.added_count).toBe(1);
			expect(store.getState().diffState.records[0]?.identifier).toBe(
				"urn:opendv:concept:fund_v12",
			);
		});

		it("P14c4::MAJ-01: detects relationship_name, grain_semantics & n-ary endpoints[2..] mutations (blocking editor, emitting remove+add for admin)", async () => {
			const sess = makeSession({
				session_id: "sess-nary-rel",
				active_version: "1.0.0",
				head_sha256: "a".repeat(64),
			});
			const c1 = makeConcept({
				id: "urn:opendv:concept:fund",
				concept_uri: "urn:opendv:concept:fund",
				concept_name: "Fund",
			});
			const c2 = makeConcept({
				id: "urn:opendv:concept:deal",
				concept_uri: "urn:opendv:concept:deal",
				concept_name: "Deal",
			});
			const c3 = makeConcept({
				id: "urn:opendv:concept:sponsor",
				concept_uri: "urn:opendv:concept:sponsor",
				concept_name: "Sponsor",
			});
			const c4 = makeConcept({
				id: "urn:opendv:concept:advisor",
				concept_uri: "urn:opendv:concept:advisor",
				concept_name: "Advisor",
			});
			const ternaryRel: RelationshipEdge = {
				id: "urn:opendv:rel:syndication",
				relationship_uri: "urn:opendv:rel:syndication",
				relationship_name: "syndicates_deal",
				target_link: "lnk_fund_deal_sponsor",
				source_concept_uri: c1.concept_uri,
				target_concept_uri: c2.concept_uri,
				endpoints: [c1.concept_uri, c2.concept_uri, c3.concept_uri],
				grain_semantics: "One record per fund-deal-sponsor tranche",
				cardinality: "many_to_many",
				description: "Original ternary syndication link",
			};

			// 1. Editor role: blocked on relationship_name, grain_semantics, and endpoints[2] edits
			const editorStore = createStudioStore({
				initialSession: sess,
				userRole: "editor",
			});
			editorStore.setState({
				nodes: [c1, c2, c3, c4],
				committedNodes: [c1, c2, c3, c4],
				edges: [ternaryRel],
				committedEdges: [ternaryRel],
				hasUnsavedChanges: false,
			});

			// updateRelationship preserves n-ary endpoints[2] when updating relationship_name
			editorStore.getState().updateRelationship(ternaryRel.id, {
				relationship_name: "co_syndicates_deal",
			});
			expect(editorStore.getState().edges[0]?.endpoints).toEqual([
				c1.concept_uri,
				c2.concept_uri,
				c3.concept_uri,
			]);
			expect(editorStore.getState().hasUnsavedChanges).toBe(true);
			const okName = await editorStore
				.getState()
				.commitChanges("Rename relationship");
			expect(okName).toBe(false);
			expect(editorStore.getState().toasts[0]?.code).toBe(
				"ONT_EVO_NON_ADDITIVE_CHANGE",
			);

			// Reset to baseline and mutate grain_semantics alone -> also blocked for editor
			editorStore.setState({
				edges: [{ ...ternaryRel, grain_semantics: "Updated grain" }],
				toasts: [],
				hasUnsavedChanges: true,
			});
			const okGrain = await editorStore
				.getState()
				.commitChanges("Change grain semantics");
			expect(okGrain).toBe(false);
			expect(editorStore.getState().toasts[0]?.code).toBe(
				"ONT_EVO_NON_ADDITIVE_CHANGE",
			);

			// Reset to baseline and mutate endpoints[2] alone -> also blocked for editor
			editorStore.setState({
				edges: [
					{
						...ternaryRel,
						endpoints: [c1.concept_uri, c2.concept_uri, c4.concept_uri],
					},
				],
				toasts: [],
				hasUnsavedChanges: true,
			});
			const okNary = await editorStore
				.getState()
				.commitChanges("Change 3rd endpoint");
			expect(okNary).toBe(false);
			expect(editorStore.getState().toasts[0]?.code).toBe(
				"ONT_EVO_NON_ADDITIVE_CHANGE",
			);

			// 2. Admin role: emits remove_relationship + add_relationship with updated name, grain_semantics & full 3-ary endpoints
			const adminStore = createStudioStore({
				initialSession: sess,
				userRole: "admin",
			});
			adminStore.setState({
				nodes: [c1, c2, c4],
				committedNodes: [c1, c2, c3, c4],
				edges: [
					{
						...ternaryRel,
						relationship_name: "co_syndicates_deal",
						grain_semantics: "One record per co-syndicated advisor tranche",
						endpoints: [c1.concept_uri, c2.concept_uri, c4.concept_uri],
						description: "Updated ternary relationship",
					},
				],
				committedEdges: [ternaryRel],
				hasUnsavedChanges: true,
			});

			let postedAdminDelta: {
				target_version: string;
				operations: Array<Record<string, unknown>>;
			} | null = null;
			const mockAdminFetch: typeof fetch = (async (url, init) => {
				const u = String(url);
				const method = (init?.method || "GET").toUpperCase();
				if (method === "POST" && u.endsWith("/delta")) {
					postedAdminDelta = JSON.parse(String(init?.body || "{}"));
					return new Response(
						JSON.stringify({
							target_version: "2.0.0",
							target_sha256: "b".repeat(64),
						}),
						{ status: 200 },
					);
				}
				if (method === "GET" && u.includes("/export")) {
					return new Response(
						JSON.stringify(
							canonicalizeGraphADict(
								adminStore.getState().nodes,
								adminStore.getState().edges,
								{ ...sess, active_version: "2.0.0" },
							),
						),
						{ status: 200 },
					);
				}
				return new Response("{}", { status: 404 });
			}) as typeof fetch;

			const adminOk = await adminStore
				.getState()
				.commitChanges("Refactor ternary relationship", mockAdminFetch);
			expect(adminOk).toBe(true);
			expect(postedAdminDelta?.target_version).toBe("2.0.0");
			const ops = postedAdminDelta?.operations ?? [];
			const removeRelIdx = ops.findIndex(
				(o) =>
					o.op_type === "remove_relationship" &&
					o.relationship_uri === ternaryRel.relationship_uri,
			);
			const removeConceptIdx = ops.findIndex(
				(o) =>
					o.op_type === "remove_concept" && o.concept_uri === c3.concept_uri,
			);
			const addRelIdx = ops.findIndex(
				(o) =>
					o.op_type === "add_relationship" &&
					o.relationship_uri === ternaryRel.relationship_uri,
			);
			expect(removeRelIdx).toBeGreaterThanOrEqual(0);
			expect(removeConceptIdx).toBeGreaterThan(removeRelIdx);
			expect(addRelIdx).toBeGreaterThan(removeConceptIdx);

			const addRelOp = ops[addRelIdx];
			expect(addRelOp?.relationship_name).toBe("co_syndicates_deal");
			expect(addRelOp?.grain_semantics).toBe(
				"One record per co-syndicated advisor tranche",
			);
			expect(addRelOp?.endpoints).toEqual([
				c1.concept_uri,
				c2.concept_uri,
				c4.concept_uri,
			]);
			expect(addRelOp?.target_link).toBe("lnk_fund_deal_sponsor");
			expect(addRelOp?.cardinality).toBe("many_to_many");
			expect(addRelOp?.description).toBe("Updated ternary relationship");
		});

		it("P14c4::MAJ-02: never wipes loadGraph HTTP 500 or concurrent SSE toasts on post-commit reload failure and sanitizes unverified user_asserted confidence", async () => {
			const sess = makeSession({
				session_id: "sess-reload-500",
				active_version: "0.0.0",
				head_sha256: GENESIS_SHA256,
				status: "EMPTY",
			});
			const store = createStudioStore({ initialSession: sess });
			store.getState().addConcept(
				makeConcept({
					confidence: 0.99,
					grounding_status: "GROUNDED",
					external_mappings: [
						{
							scheme: "FIBO",
							target_uri:
								"https://spec.edmcouncil.org/fibo/ontology/FBC/ProductsAndServices/FinancialProductsAndServices/FinancialProduct",
							predicate: "skos:exactMatch",
							confidence: 0.99,
							mapping_confidence: 0.99,
							provenance: "user_asserted",
						},
					],
				}),
			);

			const mockReload500Fetch: typeof fetch = (async (url, init) => {
				const u = String(url);
				const method = (init?.method || "GET").toUpperCase();
				if (method === "POST" && u.endsWith("/delta")) {
					return new Response(
						JSON.stringify({
							target_version: "1.0.0",
							target_sha256: "9".repeat(64),
						}),
						{ status: 200 },
					);
				}
				if (method === "GET" && u.includes("/export")) {
					// Simulate concurrent SSE/background toast arriving during loadGraph
					store.getState().addToast({
						type: "urn:opendv:warning:sse-concurrent",
						title: "Background Clarifier Notice",
						status: 409,
						detail: "Concurrent job notification while loadGraph was in-flight",
						code: "CONCURRENT_JOB_NOTICE",
					});
					return new Response(
						JSON.stringify({
							detail: "Simulated storage backend outage on export",
							code: "STORAGE_READ_ERROR",
						}),
						{ status: 500 },
					);
				}
				return new Response("{}", { status: 404 });
			}) as typeof fetch;

			const ok = await store
				.getState()
				.commitChanges("Commit with export 500", mockReload500Fetch);
			expect(ok).toBe(true);

			const toasts = store.getState().toasts;
			expect(
				toasts.some(
					(t) =>
						t.title === "Failed to Load Graph" &&
						t.status === 500 &&
						t.code === "STORAGE_READ_ERROR",
				),
			).toBe(true);
			expect(toasts.some((t) => t.code === "CONCURRENT_JOB_NOTICE")).toBe(true);

			// Unverified user_asserted confidence MUST NOT be displayed as GROUNDED when server snapshot failed to load
			const node = store.getState().nodes[0];
			const committedNode = store.getState().committedNodes[0];
			expect(node?.grounding_status).toBe("UNGROUNDED");
			expect(node?.confidence).toBe(0);
			expect(node?.external_mappings[0]?.confidence).toBeUndefined();
			expect(node?.external_mappings[0]?.mapping_confidence).toBeUndefined();
			expect(committedNode?.grounding_status).toBe("UNGROUNDED");
			expect(committedNode?.confidence).toBe(0);
			expect(committedNode?.external_mappings[0]?.confidence).toBeUndefined();
		});
	});

	// =========================================================================
	// 8. Track 3 Final Pre-Merge Remediation Suite (P14c4-MAJ-01, P14c4-MAJ-02, P14c2-MAJ-01)
	// =========================================================================
	describe("8. Track 3 Final Pre-Merge Remediation Suite (P14c4-MAJ-01, P14c4-MAJ-02, P14c2-MAJ-01)", () => {
		it("P14c4-MAJ-01: aligns SemVer target_version for refine_definition, add/remove_external_mapping, and optional vs mandatory add_attribute + blocks non-admin mandatory attribute addition", async () => {
			const baseConcept = makeConcept({
				concept_uri: "urn:opendv:concept:portfolio",
				concept_name: "Portfolio",
				definition: "Initial definition",
				attributes: [],
				external_mappings: [
					{
						scheme: "FIBO",
						target_uri:
							"https://spec.edmcouncil.org/fibo/ontology/SEC/Portfolio",
						predicate: "skos:exactMatch",
						confidence: 0.92,
					},
				],
			});

			// 1. VisualEditorDrawer defaults newAttrOptional checkbox to checked (true) and blocks mandatory attribute addition for editor
			studioStore.setState({
				activeSession: makeSession({ active_version: "1.0.0" }),
				userRole: "editor",
				nodes: [baseConcept],
				committedNodes: [baseConcept],
				selectedNodeId: baseConcept.id,
			});
			const drawerHtml = renderToStaticMarkup(
				createElement(VisualEditorDrawer),
			);
			const optCheckboxTag = drawerHtml.match(
				/<input[^>]*data-testid="new-attr-optional-checkbox"[^>]*>/,
			)?.[0];
			expect(optCheckboxTag).toBeDefined();
			expect(optCheckboxTag).toContain('checked=""');

			const mandatoryDraftErrors = validateConceptEdit(
				{
					concept_uri: baseConcept.concept_uri,
					concept_name: baseConcept.concept_name,
					elm_archetype: baseConcept.elm_archetype,
					definition: baseConcept.definition,
					target_hub: baseConcept.target_hub ?? null,
					parent_concept_uri: null,
					attributes: [
						{
							attribute_name: "mandatory_lei",
							data_type: "string",
							is_optional: false,
						},
					],
					synonyms: baseConcept.synonyms,
					external_mappings: baseConcept.external_mappings,
				},
				baseConcept,
				[baseConcept],
				{ allowDestructive: false },
			);
			expect(
				mandatoryDraftErrors.some((e) =>
					e.includes("CANNOT_ADD_MANDATORY_ATTRIBUTE"),
				),
			).toBe(true);

			const adminMandatoryDraftErrors = validateConceptEdit(
				{
					concept_uri: baseConcept.concept_uri,
					concept_name: baseConcept.concept_name,
					elm_archetype: baseConcept.elm_archetype,
					definition: baseConcept.definition,
					target_hub: baseConcept.target_hub ?? null,
					parent_concept_uri: null,
					attributes: [
						{
							attribute_name: "mandatory_lei",
							data_type: "string",
							is_optional: false,
						},
					],
					synonyms: baseConcept.synonyms,
					external_mappings: baseConcept.external_mappings,
				},
				baseConcept,
				[baseConcept],
				{ allowDestructive: true },
			);
			expect(adminMandatoryDraftErrors).toEqual([]);

			// 2. Non-admin commitChanges blocks mandatory attribute addition on committed concept with ONT_EVO_NON_ADDITIVE_CHANGE
			const editorStore = createStudioStore({
				initialSession: makeSession({
					active_version: "1.0.0",
					head_sha256: "1".repeat(64),
				}),
				userRole: "editor",
			});
			editorStore.setState({
				committedNodes: [baseConcept],
				nodes: [
					{
						...baseConcept,
						attributes: [
							{
								attribute_name: "mandatory_lei",
								data_type: "string",
								is_optional: false,
							},
						],
					},
				],
				hasUnsavedChanges: true,
			});
			let deltaCalledForEditor = false;
			const editorOk = await editorStore
				.getState()
				.commitChanges("Attempt mandatory attr as editor", (async () => {
					deltaCalledForEditor = true;
					return new Response("{}", { status: 200 });
				}) as unknown as typeof fetch);
			expect(editorOk).toBe(false);
			expect(deltaCalledForEditor).toBe(false);
			expect(
				editorStore
					.getState()
					.toasts.some((t) => t.code === "ONT_EVO_NON_ADDITIVE_CHANGE"),
			).toBe(true);

			// 3. Verify all 4 SemVer target_version rules sequentially (1.0.0 -> 1.0.1 -> 1.0.2 -> 1.0.3 -> 1.1.0 -> 2.0.0)
			const adminStore = createStudioStore({
				initialSession: makeSession({
					active_version: "1.0.0",
					head_sha256: "1".repeat(64),
				}),
				userRole: "admin",
			});
			adminStore.setState({
				committedNodes: [baseConcept],
				nodes: [baseConcept],
				hasUnsavedChanges: false,
			});

			const seenTargets: Array<{ ops: string[]; target_version: string }> = [];
			let shaStep = 1;
			const mockSemverFetch: typeof fetch = (async (url, init) => {
				const u = String(url);
				const method = (init?.method || "GET").toUpperCase();
				if (method === "POST" && u.endsWith("/delta")) {
					const body = JSON.parse(init?.body as string) as {
						target_version: string;
						operations: Array<{ op_type: string }>;
					};
					seenTargets.push({
						ops: body.operations.map((o) => o.op_type),
						target_version: body.target_version,
					});
					shaStep += 1;
					return new Response(
						JSON.stringify({
							target_version: body.target_version,
							target_sha256: String(shaStep).repeat(64).slice(0, 64),
						}),
						{ status: 200 },
					);
				}
				if (method === "GET" && u.includes("/export")) {
					const currNodes = adminStore.getState().committedNodes;
					return new Response(
						JSON.stringify({
							ontology_version:
								seenTargets[seenTargets.length - 1]?.target_version || "1.0.0",
							domain_namespace: "private_equity",
							enterprise_owner: "Steward Team",
							canonical_business_concepts: currNodes,
							canonical_relationships: [],
						}),
						{ status: 200 },
					);
				}
				return new Response("{}", { status: 404 });
			}) as typeof fetch;

			// Op A: refine_definition -> PATCH (1.0.1)
			adminStore
				.getState()
				.updateConcept(baseConcept.id, { definition: "Refined v1.0.1" });
			expect(
				await adminStore
					.getState()
					.commitChanges("Refine def", mockSemverFetch),
			).toBe(true);
			expect(seenTargets[0]).toEqual({
				ops: ["refine_definition"],
				target_version: "1.0.1",
			});

			// Op B: add_external_mapping -> PATCH (1.0.2)
			const cAfterDef = adminStore.getState().nodes[0]!;
			adminStore.getState().updateConcept(baseConcept.id, {
				external_mappings: [
					...cAfterDef.external_mappings,
					{
						scheme: "ISO20022",
						target_uri: "urn:iso20022:Portfolio",
						predicate: "skos:closeMatch",
						confidence: 0.82,
					},
				],
			});
			expect(
				await adminStore
					.getState()
					.commitChanges("Add mapping", mockSemverFetch),
			).toBe(true);
			expect(seenTargets[1]).toEqual({
				ops: ["add_external_mapping"],
				target_version: "1.0.2",
			});

			// Op C: remove_external_mapping -> PATCH (1.0.3)
			const cAfterAddMap = adminStore.getState().nodes[0]!;
			adminStore.getState().updateConcept(baseConcept.id, {
				external_mappings: cAfterAddMap.external_mappings.slice(0, 1),
			});
			expect(
				await adminStore
					.getState()
					.commitChanges("Remove mapping", mockSemverFetch),
			).toBe(true);
			expect(seenTargets[2]).toEqual({
				ops: ["remove_external_mapping"],
				target_version: "1.0.3",
			});

			// Op D: add_attribute (is_optional: true) -> MINOR (1.1.0)
			adminStore.getState().updateConcept(baseConcept.id, {
				attributes: [
					{
						attribute_name: "opt_vintage_year",
						data_type: "integer",
						is_optional: true,
					},
				],
			});
			expect(
				await adminStore
					.getState()
					.commitChanges("Add optional attr", mockSemverFetch),
			).toBe(true);
			expect(seenTargets[3]).toEqual({
				ops: ["add_attribute"],
				target_version: "1.1.0",
			});

			// Op E: add_attribute (is_optional: false) as admin -> MAJOR (2.0.0)
			const cAfterOptAttr = adminStore.getState().nodes[0]!;
			adminStore.getState().updateConcept(baseConcept.id, {
				attributes: [
					...cAfterOptAttr.attributes,
					{
						attribute_name: "mandatory_fund_code",
						data_type: "string",
						is_optional: false,
					},
				],
			});
			expect(
				await adminStore
					.getState()
					.commitChanges("Add mandatory attr", mockSemverFetch),
			).toBe(true);
			expect(seenTargets[4]).toEqual({
				ops: ["add_attribute"],
				target_version: "2.0.0",
			});
		});

		it("P14c4-MAJ-02: hydrates committed JCS FIBO concepts without numeric confidence as GROUNDED and delta-scopes ONT-EVO-002 precheck on Commit 2", async () => {
			const store = createStudioStore({
				initialSession: makeSession({
					session_id: "sess-unscored-fibo",
					active_version: "0.0.0",
					head_sha256: GENESIS_SHA256,
					status: "EMPTY",
				}),
			});

			// Commit 1: Steward creates Portfolio with UI-authored user_asserted FIBO skos:exactMatch
			store.getState().addConcept(
				makeConcept({
					concept_uri: "urn:opendv:concept:portfolio",
					concept_name: "Portfolio",
					external_mappings: [
						{
							scheme: "FIBO",
							target_uri:
								"https://spec.edmcouncil.org/fibo/ontology/SEC/Portfolio",
							predicate: "skos:exactMatch",
							provenance: "user_asserted",
							confidence: 0.85,
						},
					],
				}),
			);

			let serverVersion = "0.0.0";
			let serverSha = GENESIS_SHA256;
			let serverConcepts: Array<Record<string, unknown>> = [];
			let serverRelationships: Array<Record<string, unknown>> = [];

			const mockLiveBackendFetch: typeof fetch = (async (url, init) => {
				const u = String(url);
				const method = (init?.method || "GET").toUpperCase();
				if (method === "POST" && u.endsWith("/delta")) {
					const body = JSON.parse(init?.body as string) as {
						target_version: string;
						operations: Array<Record<string, unknown>>;
					};
					serverVersion = body.target_version;
					serverSha =
						body.target_version === "1.0.0"
							? "a".repeat(64)
							: body.target_version === "1.0.1"
								? "b".repeat(64)
								: "c".repeat(64);
					for (const op of body.operations) {
						if (op.op_type === "add_concept") {
							// Match real AtomicCommitService: JCS ontology.json omits confidence on ExternalMapping
							const rawMappings =
								(op.external_mappings as Array<Record<string, unknown>>) || [];
							serverConcepts.push({
								concept_uri: op.concept_uri,
								concept_name: op.concept_name,
								elm_archetype: op.elm_archetype,
								definition: op.definition,
								target_hub: op.target_hub ?? null,
								parent_concept_uri: op.parent_concept_uri ?? null,
								attributes: [],
								synonyms: op.synonyms || [],
								external_mappings: rawMappings.map((m) => ({
									predicate: m.predicate,
									scheme: m.scheme,
									target_uri: m.target_uri,
								})),
							});
						} else if (op.op_type === "refine_definition") {
							serverConcepts = serverConcepts.map((c) =>
								c.concept_uri === op.concept_uri
									? { ...c, definition: op.definition }
									: c,
							);
						} else if (op.op_type === "add_relationship") {
							serverRelationships.push({
								relationship_uri: op.relationship_uri,
								relationship_name: op.relationship_name,
								target_link: op.target_link,
								endpoints: op.endpoints,
							});
						}
					}
					return new Response(
						JSON.stringify({
							target_version: serverVersion,
							target_sha256: serverSha,
						}),
						{ status: 200 },
					);
				}
				if (method === "GET" && u.includes("/export")) {
					return new Response(
						JSON.stringify({
							ontology_version: serverVersion,
							domain_namespace: "private_equity",
							enterprise_owner: "Steward Team",
							canonical_business_concepts: serverConcepts,
							canonical_relationships: serverRelationships,
						}),
						{ status: 200 },
					);
				}
				return new Response("{}", { status: 404 });
			}) as typeof fetch;

			// Execute Commit 1 (0.0.0 -> 1.0.0)
			const ok1 = await store
				.getState()
				.commitChanges("Genesis FIBO concept", mockLiveBackendFetch);
			expect(ok1).toBe(true);

			// Reloaded v1.0.0 concept has NO numeric confidence key on ExternalMapping, yet MUST be GROUNDED
			const v1Portfolio = store.getState().nodes[0]!;
			expect(v1Portfolio.grounding_status).toBe("GROUNDED");
			expect(v1Portfolio.external_mappings[0]?.confidence).toBeUndefined();
			expect(
				v1Portfolio.external_mappings[0]?.mapping_confidence,
			).toBeUndefined();
			expect(store.getState().qualityScorecard.grounding_score).toBe(1);
			const evoAfterCommit1 = store
				.getState()
				.qualityScorecard.checks.find((c) => c.rule_id === "ONT-EVO-002");
			expect(evoAfterCommit1?.status).toBe("PASS");

			// Simulate an untouched historical concept committed under a prior browser session's waiver
			// (no FIBO mapping, and sessionWaivers in memory is empty after browser reload)
			serverConcepts.push({
				concept_uri: "urn:opendv:concept:historical_waived",
				concept_name: "Historical Waived Concept",
				elm_archetype: "CORE_BUSINESS_CONCEPT",
				definition: "Previously waived in an earlier steward browser session.",
				target_hub: "hub_historical",
				attributes: [],
				synonyms: [],
				external_mappings: [],
			});
			await store.getState().loadGraph("1.0.0", mockLiveBackendFetch);
			expect(store.getState().nodes).toHaveLength(2);
			const reloadedPortfolio = store
				.getState()
				.nodes.find((n) => n.concept_uri === "urn:opendv:concept:portfolio")!;
			const reloadedWaived = store
				.getState()
				.nodes.find(
					(n) => n.concept_uri === "urn:opendv:concept:historical_waived",
				)!;
			expect(reloadedPortfolio.grounding_status).toBe("GROUNDED");
			expect(reloadedWaived.grounding_status).toBe("UNGROUNDED");

			// Editing definition on v1.0.0 Portfolio preserves GROUNDED status and commits cleanly as 1.0.1 (PATCH)
			// without wedging on the untouched historical_waived concept!
			store.getState().updateConcept(reloadedPortfolio.id, {
				definition: "Refined institutional portfolio definition.",
			});
			expect(
				store
					.getState()
					.nodes.find((n) => n.concept_uri === "urn:opendv:concept:portfolio")
					?.grounding_status,
			).toBe("GROUNDED");
			const okDef = await store
				.getState()
				.commitChanges("Refine portfolio definition", mockLiveBackendFetch);
			expect(okDef).toBe(true);
			expect(store.getState().activeSession?.active_version).toBe("1.0.1");

			// Commit 2 (1.0.1 -> 1.1.0): Add a second UI-authored FIBO concept + relationship
			store.getState().addConcept(
				makeConcept({
					id: "urn:opendv:concept:deal",
					concept_uri: "urn:opendv:concept:deal",
					concept_name: "Deal",
					target_hub: "hub_deal",
					external_mappings: [
						{
							scheme: "FIBO",
							target_uri:
								"https://spec.edmcouncil.org/fibo/ontology/FBC/Transactions/Deal",
							predicate: "skos:closeMatch",
							provenance: "user_asserted",
							confidence: 0.85,
						},
					],
				}),
			);
			store.getState().addRelationship({
				id: "urn:opendv:rel:portfolio_holds_deal",
				relationship_uri: "urn:opendv:rel:portfolio_holds_deal",
				relationship_name: "holds_deal",
				target_link: "lnk_portfolio_deal",
				source_concept_uri: "urn:opendv:concept:portfolio",
				target_concept_uri: "urn:opendv:concept:deal",
			});

			const ok2 = await store
				.getState()
				.commitChanges("Add Deal and relationship", mockLiveBackendFetch);
			expect(ok2).toBe(true);
			expect(store.getState().activeSession?.active_version).toBe("1.1.0");
			expect(
				store
					.getState()
					.nodes.find((n) => n.concept_uri === "urn:opendv:concept:deal")
					?.grounding_status,
			).toBe("GROUNDED");

			// Verify safety guard: adding a genuinely ungrounded concept (no FIBO mapping) STILL fails ONT_EVO_002_FAILED!
			store.getState().addConcept(
				makeConcept({
					id: "urn:opendv:concept:ungrounded_draft",
					concept_uri: "urn:opendv:concept:ungrounded_draft",
					concept_name: "Ungrounded Draft",
					grounding_status: "UNGROUNDED",
					external_mappings: [],
				}),
			);
			const blocked = await store
				.getState()
				.commitChanges("Attempt ungrounded commit", mockLiveBackendFetch);
			expect(blocked).toBe(false);
			expect(
				store.getState().toasts.some((t) => t.code === "ONT_EVO_002_FAILED"),
			).toBe(true);

			// Verify removing all mappings on Portfolio still forces UNGROUNDED
			store.getState().updateConcept("urn:opendv:concept:portfolio", {
				external_mappings: [],
			});
			expect(
				store
					.getState()
					.nodes.find((n) => n.concept_uri === "urn:opendv:concept:portfolio")
					?.grounding_status,
			).toBe("UNGROUNDED");
		});

		it("P14c2-MAJ-01: loadSessions preserves activeSession.head_sha256 & enriched metadata and never clobbers isConstructing when StudioSessionSummaryDTO omits head_sha256", async () => {
			const richActive = makeSession({
				session_id: "sess-live-construct",
				active_version: "1.0.0",
				head_sha256: "d".repeat(64),
				status: "COMMITTED",
				grandfathered_concept_uris: ["urn:legacy:entity_1"],
				commits: [
					{
						version: "1.0.0",
						sha256: "d".repeat(64),
						parent_sha256: GENESIS_SHA256,
						timestamp: "2026-11-13T00:00:00Z",
						author: "Steward Team",
						rationale: "Initial commit",
						operations_count: 1,
					},
				],
			});
			const store = createStudioStore({ initialSession: richActive });
			store.setState({
				sessions: [richActive],
				isConstructing: true,
				hasUnsavedChanges: false,
			});

			let selectSessionTriggered = false;
			const mockSummaryListFetch: typeof fetch = (async (url) => {
				const u = String(url);
				if (u.endsWith("/sessions")) {
					// Match backend list_studio_sessions: StudioSessionSummaryDTO omits head_sha256, commits, grandfathered_concept_uris
					return new Response(
						JSON.stringify([
							{
								session_id: "sess-live-construct",
								domain_namespace: "private_equity",
								enterprise_owner: "Steward Team",
								status: "COMMITTED",
								active_version: "1.0.0",
								created_at: "2026-11-13T00:00:00Z",
								updated_at: "2026-11-13T01:00:00Z",
							},
						]),
						{ status: 200 },
					);
				}
				selectSessionTriggered = true;
				return new Response("{}", { status: 200 });
			}) as typeof fetch;

			await store.getState().loadSessions(mockSummaryListFetch);

			expect(selectSessionTriggered).toBe(false);
			expect(store.getState().isConstructing).toBe(true);
			expect(store.getState().activeSession?.head_sha256).toBe("d".repeat(64));
			expect(
				store.getState().activeSession?.grandfathered_concept_uris,
			).toEqual(["urn:legacy:entity_1"]);
			expect(store.getState().activeSession?.commits).toHaveLength(1);
			expect(store.getState().activeSession?.updated_at).toBe(
				"2026-11-13T01:00:00Z",
			);

			// Even if active_version advances on the server while isConstructing is true, loadSessions must NOT abort the active job via selectSession
			const mockAdvancedWhileConstructingFetch: typeof fetch = (async (url) => {
				const u = String(url);
				if (u.endsWith("/sessions")) {
					return new Response(
						JSON.stringify([
							{
								session_id: "sess-live-construct",
								domain_namespace: "private_equity",
								enterprise_owner: "Steward Team",
								status: "COMMITTED",
								active_version: "1.1.0",
								created_at: "2026-11-13T00:00:00Z",
								updated_at: "2026-11-13T02:00:00Z",
							},
						]),
						{ status: 200 },
					);
				}
				selectSessionTriggered = true;
				return new Response("{}", { status: 200 });
			}) as typeof fetch;

			await store.getState().loadSessions(mockAdvancedWhileConstructingFetch);
			expect(selectSessionTriggered).toBe(false);
			expect(store.getState().isConstructing).toBe(true);
		});
	});
});
