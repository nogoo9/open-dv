/**
 * tests/gt05-render.test.ts — GT-05 Golden Text Render Snapshot Test Suite.
 *
 * Verifies that `renderExplorerTextSnapshot`:
 * 1. Reads GT-01 Wave 3 bundle (`tests/golden/GT-01/wave3/explorer_bundle.json`).
 * 2. Compares deterministic text snapshot against `tests/golden/GT-05/render_snapshot.txt`.
 * 3. Asserts exact 0-diff match with 100% bit-for-bit parity.
 * 4. Deterministically renders steward hold warning banners and handles snake_case/camelCase keys.
 */

import { describe, expect, it } from "bun:test";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import {
	type ExplorerBundleSnapshotInput,
	renderExplorerTextSnapshot,
} from "../src/explorer/render-text-snapshot";

describe("GT-05 Golden Text Render Snapshot", () => {
	const repoRoot = resolve(__dirname, "../../../");
	const wave3BundlePath = resolve(
		repoRoot,
		"tests/golden/GT-01/wave3/explorer_bundle.json",
	);
	const gt05GoldenPath = resolve(
		repoRoot,
		"tests/golden/GT-05/render_snapshot.txt",
	);

	it("renders GT-01 Wave 3 explorer bundle with 0-diff match against GT-05 golden text snapshot", () => {
		const bundleContent = readFileSync(wave3BundlePath, "utf-8");
		const bundle = JSON.parse(bundleContent) as ExplorerBundleSnapshotInput;
		const expectedGolden = readFileSync(gt05GoldenPath, "utf-8");

		const actualSnapshot = renderExplorerTextSnapshot(bundle);

		expect(actualSnapshot).toBe(expectedGolden);
		expect(actualSnapshot.length).toBe(expectedGolden.length);
	});

	it("produces byte-stable deterministic output across multiple invocations", () => {
		const bundleContent = readFileSync(wave3BundlePath, "utf-8");
		const bundle = JSON.parse(bundleContent) as ExplorerBundleSnapshotInput;

		const run1 = renderExplorerTextSnapshot(bundle);
		const run2 = renderExplorerTextSnapshot(bundle);
		const run3 = renderExplorerTextSnapshot(bundle);

		expect(run1).toBe(run2);
		expect(run2).toBe(run3);
	});

	it("renders steward hold warning banner when hold status is active", () => {
		const holdBundle: ExplorerBundleSnapshotInput = {
			displayName: "Test Project",
			specFile: "pipeline.yaml",
			statusLabel: "PAUSED_HOLD",
			wavesCount: 1,
			rbacRole: "DATA_STEWARD",
			gatesPassed: 2,
			gatesTotal: 6,
			stages: [
				{
					key: "STAGE_00",
					name: "Stage Zero",
					statusTag: "PASS",
				},
				{
					key: "STAGE_01",
					name: "Stage One",
					statusTag: "HOLD",
				},
			],
			artifacts: {
				art1: {
					group: "Input",
					name: "input.csv",
					format: "CSV",
				},
			},
		};

		const snapshot = renderExplorerTextSnapshot(holdBundle);

		expect(snapshot).toContain("⚠️ STEWARD HOLD ACTIVE: Unstructured extraction requires review");
		expect(snapshot).toContain("Status:      PAUSED_HOLD");
		expect(snapshot).toContain("[HOLD          ] STAGE_01             Stage One");
		expect(snapshot).toContain("* art1            [Input       ] [CSV  ] input.csv");
	});

	it("handles snake_case and camelCase input properties with identical snapshot fidelity", () => {
		const snakeBundle: ExplorerBundleSnapshotInput = {
			display_name: "Unified Pipeline",
			spec_file: "input.yaml",
			status_label: "Sealed",
			waves_count: 2,
			rbac_role: "DATA_STEWARD",
			gates_passed: 6,
			gates_total: 6,
			stages: [
				{
					key: "STAGE_00",
					name: "Orchestration",
					status_tag: "PASS",
				},
			],
			artifacts: {
				contract: {
					group: "Output",
					name: "final_contract.yaml",
					format: "YAML",
				},
			},
		};

		const camelBundle: ExplorerBundleSnapshotInput = {
			displayName: "Unified Pipeline",
			specFile: "input.yaml",
			statusLabel: "Sealed",
			wavesCount: 2,
			rbacRole: "DATA_STEWARD",
			gatesPassed: 6,
			gatesTotal: 6,
			stages: [
				{
					key: "STAGE_00",
					name: "Orchestration",
					statusTag: "PASS",
				},
			],
			artifacts: {
				contract: {
					group: "Output",
					name: "final_contract.yaml",
					format: "YAML",
				},
			},
		};

		const snakeSnapshot = renderExplorerTextSnapshot(snakeBundle);
		const camelSnapshot = renderExplorerTextSnapshot(camelBundle);

		expect(snakeSnapshot).toBe(camelSnapshot);
	});
});
