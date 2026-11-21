/**
 * M6 Gen2 Failing-First TDD Tests — Step 6 SSE `run_complete` Drift Hydration
 * & Structured `syncDriftToStep4` Loopback (`UAT-14`).
 * (`apps/opendv-web-workbench/tests/m6-step6-drift-loopback.test.ts`)
 */

import { describe, expect, it } from "bun:test";
import { buildDeltaOperationsAndVersion } from "../src/ontology/store/graph-commit-ops";
import { studioStore } from "../src/ontology/store/studio-store";
import { fetchRunHydrationBundle } from "../src/ontology/wizard/step6-run-hydration";
import {
	deriveStep4State,
	syncStep4DecisionsToStore,
} from "../src/ontology/wizard/wizard-step4-adapter";

const SESSION_DRIFT = {
	session_id: "s-drift-loopback-01",
	domain_namespace: "wealth_mgmt",
	enterprise_owner: "Enterprise",
	status: "COMMITTED" as const,
	active_version: "1.1.0",
	head_sha256: "c".repeat(64),
	created_at: "2026-11-19T18:00:00Z",
	updated_at: "2026-11-19T23:04:12Z",
	wizard_schema_version: 2,
};

const DRIFT_FINDINGS_FIXTURE = [
	{
		code: "UF-1",
		category: "ADDITIVE",
		drift_type: "ADDITIVE_COLUMN",
		suggested_op: "SPLIT_SATELLITE",
		source_table: "ds03_w3_positions.positions",
		column_name: "esg_score",
	},
	{
		code: "UF-1",
		category: "ADDITIVE",
		drift_type: "ADDITIVE_COLUMN",
		suggested_op: "SPLIT_SATELLITE",
		source_table: "ds03_w3_positions.positions",
		column_name: "risk_bucket",
	},
	{
		code: "UF-3",
		category: "SOFT_DEPRECATED",
		drift_type: "COLUMN_REMOVAL",
		suggested_op: "SOFT_DEPRECATE_NULL",
		source_table: "ds03_w3_positions.positions",
		column_name: "legacy_desk_memo",
	},
	{
		code: "UF-2",
		category: "RENAMED",
		drift_type: "COLUMN_RENAME",
		suggested_op: "ALIAS_IN_ZONE1",
		source_table: "ds03_w3_positions.positions",
		column_name: "market_value",
		new_column_name: "mkt_val_usd",
	},
];

describe("M6 Gen2: Step 6 SSE run_complete driftColumns hydration & structured syncDriftToStep4", () => {
	it("populates driftColumns & driftFindings from report bundle on run_complete even when SSE data omits d.drift_columns", async () => {
		studioStore.getState().reset();
		studioStore.setState({ activeSession: SESSION_DRIFT });

		const listeners = new Map<
			string,
			Array<(ev: unknown) => Promise<void> | void>
		>();
		const origEs = (globalThis as Record<string, unknown>).EventSource;
		class MockEs {
			addEventListener(
				type: string,
				cb: (ev: unknown) => Promise<void> | void,
			) {
				const arr = listeners.get(type) ?? [];
				arr.push(cb);
				listeners.set(type, arr);
			}
			close() {}
		}
		(globalThis as Record<string, unknown>).EventSource = MockEs;

		const mockFetch = (async (input: RequestInfo | URL) => {
			const url = String(input);
			if (url.includes("/export-blueprint")) {
				return new Response(
					JSON.stringify({ blueprint: { domain_namespace: "wealth_mgmt" } }),
					{ status: 200 },
				);
			}
			if (url.endsWith("/api/v1/pipelines/runs")) {
				return new Response(JSON.stringify({ run_id: "run-drift-sse-1" }), {
					status: 202,
				});
			}
			if (url.includes("/api/v1/runs/run-drift-sse-1/report")) {
				return new Response(
					JSON.stringify({
						status: "COMPLETED",
						step_reports: [
							{
								stage_id: "STAGE_07_DETERMINISTIC_COMPILER",
								status: "PASS",
								verdict: "PASS",
							},
						],
						drift_findings: DRIFT_FINDINGS_FIXTURE,
						four_zone_sql: { duckdb: { zone_0_staging: "SELECT 1;" } },
					}),
					{ status: 200 },
				);
			}
			return new Response("{}", { status: 200 });
		}) as unknown as typeof fetch;

		try {
			const bundle = await fetchRunHydrationBundle(
				async (f, u, init) => f(u, init),
				mockFetch,
				{ run_id: "run-drift-sse-1", status: "COMPLETED" },
				["duckdb"],
			);
			expect(
				(bundle as unknown as { driftFindings?: unknown[] }).driftFindings,
			).toHaveLength(4);

			await studioStore.getState().startDvPipelineRun(undefined, mockFetch);
			for (const cb of listeners.get("run_complete") ?? []) {
				// Simulate SSE broadcaster payload omitting d.drift_columns
				await cb({ data: JSON.stringify({ status: "COMPLETED" }) });
			}

			expect(studioStore.getState().driftColumns).toEqual([
				"esg_score",
				"risk_bucket",
				"legacy_desk_memo",
				"market_value",
			]);
		} finally {
			(globalThis as Record<string, unknown>).EventSource = origEs;
			studioStore.getState().reset();
		}
	});

	it("updates driftColumns from d.drift_columns on run_complete and emits structured additive/drop/rename steering_prompt in syncDriftToStep4", async () => {
		studioStore.getState().reset();
		studioStore.setState({ activeSession: SESSION_DRIFT });

		const listeners = new Map<
			string,
			Array<(ev: unknown) => Promise<void> | void>
		>();
		const origEs = (globalThis as Record<string, unknown>).EventSource;
		class MockEs {
			addEventListener(
				type: string,
				cb: (ev: unknown) => Promise<void> | void,
			) {
				const arr = listeners.get(type) ?? [];
				arr.push(cb);
				listeners.set(type, arr);
			}
			close() {}
		}
		(globalThis as Record<string, unknown>).EventSource = MockEs;

		let capturedConstructBody: Record<string, unknown> | null = null;
		const mockFetch = (async (input: RequestInfo | URL, init?: RequestInit) => {
			const url = String(input);
			if (url.includes("/export-blueprint")) {
				return new Response(
					JSON.stringify({ blueprint: { domain_namespace: "wealth_mgmt" } }),
					{ status: 200 },
				);
			}
			if (url.endsWith("/api/v1/pipelines/runs")) {
				return new Response(JSON.stringify({ run_id: "run-drift-sse-2" }), {
					status: 202,
				});
			}
			if (url.includes("/api/v1/runs/run-drift-sse-2/report")) {
				return new Response(
					JSON.stringify({
						status: "COMPLETED",
						step_reports: [],
						drift_findings: DRIFT_FINDINGS_FIXTURE,
					}),
					{ status: 200 },
				);
			}
			if (url.endsWith(`/sessions/${SESSION_DRIFT.session_id}/construct`)) {
				capturedConstructBody = JSON.parse(
					String(init?.body ?? "{}"),
				) as Record<string, unknown>;
				return new Response(JSON.stringify({ job_id: "job-construct-drift" }), {
					status: 202,
				});
			}
			return new Response("{}", { status: 200 });
		}) as unknown as typeof fetch;

		try {
			await studioStore.getState().startDvPipelineRun(undefined, mockFetch);
			for (const cb of listeners.get("run_complete") ?? []) {
				await cb({
					data: JSON.stringify({
						status: "COMPLETED",
						drift_columns: [
							"esg_score",
							"risk_bucket",
							"legacy_desk_memo",
							"market_value",
						],
						drift_findings: DRIFT_FINDINGS_FIXTURE,
					}),
				});
			}

			expect(studioStore.getState().driftColumns).toEqual([
				"esg_score",
				"risk_bucket",
				"legacy_desk_memo",
				"market_value",
			]);

			const ok = await studioStore.getState().syncDriftToStep4(mockFetch);
			expect(ok).toBe(true);
			expect(capturedConstructBody).not.toBeNull();
			const steer = String(capturedConstructBody?.steering_prompt ?? "");
			expect(steer).toContain("[+esg_score, risk_bucket]");
			expect(steer).toContain("[-legacy_desk_memo]");
			expect(steer).toContain("MAJOR · Opt-In remove_attribute");
			expect(steer).toContain("market_value -> mkt_val_usd");
		} finally {
			(globalThis as Record<string, unknown>).EventSource = origEs;
			studioStore.getState().reset();
		}
	});

	it("preserves add_attribute evidence_refs across Step 4 sync->delta and supports Opt-In remove_attribute MAJOR toggle", async () => {
		studioStore.getState().reset();
		const posNode = {
			id: "urn:ontology:entity:position",
			concept_uri: "urn:ontology:entity:position",
			concept_name: "Position",
			elm_archetype: "CORE_BUSINESS_CONCEPT",
			definition: "Institutional holding",
			grounding_status: "GROUNDED" as const,
			attributes: [
				{
					attribute_name: "market_value",
					data_type: "decimal",
					is_optional: true,
				},
				{
					attribute_name: "legacy_desk_memo",
					data_type: "string",
					is_optional: true,
				},
			],
			synonyms: [],
			external_mappings: [],
		};
		studioStore.setState({
			activeSession: SESSION_DRIFT,
			nodes: [posNode],
			committedNodes: [posNode],
			proposals: [
				{
					proposal_id: "prop-w3-drift",
					status: "PENDING",
					base_version: "1.1.0",
					operations: [
						{
							op_id: "op-add-buf",
							op_type: "add_attribute",
							concept_uri: "urn:ontology:entity:position",
							attribute_name: "intraday_margin_buffer_bps",
							data_type: "decimal",
							is_optional: true,
							evidence_refs: [
								{
									evidence_type: "column_profile",
									bronze_table: "positions",
									column_name: "intraday_margin_buffer_bps",
									table_id: "tbl-ds03-pos",
								},
							],
						},
						{
							op_id: "op-rem-memo",
							op_type: "remove_attribute",
							concept_uri: "urn:ontology:entity:position",
							attribute_name: "legacy_desk_memo",
							reason: "Dropped in DS-03",
						},
					],
				},
			],
		});

		// 1. Default unchecked MAJOR Opt-In -> preserves legacy_desk_memo, sends evidence_refs on add_attribute, bumps MINOR 1.2.0
		const { concepts, breakingCandidates } = deriveStep4State(
			studioStore.getState().proposals,
			[],
		);
		expect(breakingCandidates).toHaveLength(1);
		expect(breakingCandidates[0]?.checked).toBe(false);
		await syncStep4DecisionsToStore(
			studioStore.getState(),
			concepts,
			breakingCandidates,
		);
		const updatedPos = studioStore
			.getState()
			.nodes.find((n) => n.concept_uri === "urn:ontology:entity:position");
		expect(
			updatedPos?.attributes?.some(
				(a) => a.attribute_name === "legacy_desk_memo",
			),
		).toBe(true);
		const bufAttr = updatedPos?.attributes?.find(
			(a) => a.attribute_name === "intraday_margin_buffer_bps",
		);
		expect(bufAttr?.evidence_refs).toHaveLength(1);

		const minorDelta = buildDeltaOperationsAndVersion({
			baseVersion: "1.1.0",
			committedNodes: studioStore.getState().committedNodes,
			currentNodes: studioStore.getState().nodes,
			committedEdges: [],
			currentEdges: [],
			committedNodeMap: new Map([[posNode.concept_uri, posNode]]),
			committedEdgeMap: new Map(),
			removedConcepts: [],
			removedEdges: [],
			modifiedEdgeUris: new Set(),
			modifiedCommittedEdges: [],
		});
		expect(minorDelta.targetVersion).toBe("1.2.0");
		const addBufOp = minorDelta.operations.find(
			(op) =>
				op.op_type === "add_attribute" &&
				op.attribute_name === "intraday_margin_buffer_bps",
		);
		expect(Array.isArray(addBufOp?.evidence_refs)).toBe(true);
		expect((addBufOp?.evidence_refs as unknown[]).length).toBe(1);

		// 2. Checked MAJOR Opt-In -> removes legacy_desk_memo & bumps MAJOR 2.0.0
		const optedIn = breakingCandidates.map((b) => ({ ...b, checked: true }));
		await syncStep4DecisionsToStore(studioStore.getState(), concepts, optedIn);
		const posAfterOptIn = studioStore
			.getState()
			.nodes.find((n) => n.concept_uri === "urn:ontology:entity:position");
		expect(
			posAfterOptIn?.attributes?.some(
				(a) => a.attribute_name === "legacy_desk_memo",
			),
		).toBe(false);
		const majorDelta = buildDeltaOperationsAndVersion({
			baseVersion: "1.1.0",
			committedNodes: studioStore.getState().committedNodes,
			currentNodes: studioStore.getState().nodes,
			committedEdges: [],
			currentEdges: [],
			committedNodeMap: new Map([[posNode.concept_uri, posNode]]),
			committedEdgeMap: new Map(),
			removedConcepts: [],
			removedEdges: [],
			modifiedEdgeUris: new Set(),
			modifiedCommittedEdges: [],
		});
		expect(majorDelta.targetVersion).toBe("2.0.0");
		expect(
			majorDelta.operations.some(
				(op) =>
					op.op_type === "remove_attribute" &&
					op.attribute_name === "legacy_desk_memo",
			),
		).toBe(true);

		studioStore.getState().reset();
	});
});
