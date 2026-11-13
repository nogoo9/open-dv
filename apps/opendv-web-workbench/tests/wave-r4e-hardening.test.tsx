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

			// Minimal mock without GET /export: must NOT fabricate 1.0/0.85 or verified_pack
			const fallbackStore = createStudioStore({ initialSession: session });
			fallbackStore.getState().addConcept(userAssertedNode);
			const mockMinimalFetch: typeof fetch = (async (url, init) => {
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
				return new Response("{}", { status: 404 });
			}) as typeof fetch;

			const fallbackOk = await fallbackStore
				.getState()
				.commitChanges("Commit fallback test", mockMinimalFetch);
			expect(fallbackOk).toBe(true);
			const fallbackMapping =
				fallbackStore.getState().nodes[0]?.external_mappings?.[0];
			expect(fallbackMapping?.mapping_confidence).toBeUndefined();
			expect(fallbackMapping?.confidence).toBeUndefined();
			expect(fallbackMapping?.provenance).toBe("user_asserted");
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
});
