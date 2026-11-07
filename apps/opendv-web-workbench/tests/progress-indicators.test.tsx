/**
 * Unit & Component Specification Tests for Wave 2 — Track B1:
 * - `PipelineRunProgressBar` across `running`, `hold`, `failed`, and `sealed` states
 * - Native `<progress>` semantics & `modern-web-guidance` compliance
 * - 8-stage stepper pills (`00`..`07`) states & click callbacks
 * - `OverviewStagesTrack` & `PipelineOverviewView` per-stage running/completed/queued indicators
 * - `buildStaticWorkbenchAssets` static asset builder
 */

import { describe, expect, it } from "bun:test";
import { existsSync, mkdtempSync, readFileSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import type { Stage } from "@opendv/protocol-ts";
import { renderToStaticMarkup } from "react-dom/server";
import {
	buildStaticWorkbenchAssets,
	generateStaticIndexHtml,
} from "../src/build-static";
import { OverviewStagesTrack } from "../src/explorer/overview/OverviewStagesTrack";
import { PipelineOverviewView } from "../src/explorer/overview/PipelineOverviewView";
import {
	PipelineRunProgressBar,
	computeCompletedStagesCount,
	getStepperPillState,
} from "../src/explorer/progress/PipelineRunProgressBar";
import { GLOBAL_COMMERCE_EXPLORER_BUNDLE } from "./fixtures/commerce-explorer-fixture";

function cloneStagesWithTags(
	overrides: Partial<Record<number, Stage["tag"]>> = {},
): Stage[] {
	return GLOBAL_COMMERCE_EXPLORER_BUNDLE.stages.map((st, idx) => ({
		...st,
		tag: overrides[idx] ?? "PASS",
	}));
}

describe("Wave 2 Track B1: Pipeline Run Progress Indicators & Static Asset Builder", () => {
	describe("1. PipelineRunProgressBar across running, hold, failed, and sealed states", () => {
		it("renders RUNNING state with active stage pill, telemetry counters, native <progress>, and stepper states", () => {
			const stages = cloneStagesWithTags();
			const html = renderToStaticMarkup(
				<PipelineRunProgressBar
					stages={stages}
					runState="running"
					activeRunningStageIdx={3}
					statusBannerText="Running STAGE_03_SHIP_VAULT_PASS1"
					streamedTokens={1280}
					eventCount={42}
				/>,
			);

			expect(html).toContain('data-testid="pipeline-run-progress-bar"');
			expect(html).toContain('role="status"');
			expect(html).toContain('aria-live="polite"');
			expect(html).toContain('data-testid="pipeline-run-status-badge"');
			expect(html).toContain("RUNNING");
			expect(html).toContain("#1D4ED8");

			// Stage progress summary: activeRunningStageIdx=3 -> Stage 04 of 08, completedCount=3 -> 38%
			expect(html).toContain('data-testid="pipeline-progress-summary"');
			expect(html).toContain("Stage 04 of 08 · 38%");

			// Active stage & persona pill
			expect(html).toContain('data-testid="pipeline-active-stage-pill"');
			expect(html).toContain("STAGE_03 · @ship-vault");

			// Telemetry counters
			expect(html).toContain('data-testid="pipeline-telemetry-events"');
			expect(html).toContain("42 events");
			expect(html).toContain('data-testid="pipeline-telemetry-tokens"');
			expect(html).toContain("1280 tokens");

			// Native <progress> element with max=8, value=3, aria-label, and no tabindex
			expect(html).toContain('data-testid="pipeline-native-progress"');
			expect(html).toContain('aria-label="Pipeline stage completion progress"');
			expect(html).toContain('max="8"');
			expect(html).toContain('value="3"');
			expect(html).not.toMatch(/<progress[^>]*tabindex=/i);
			expect(html).toContain("></progress>");

			// Visual fill bar width
			expect(html).toContain('data-testid="pipeline-progress-fill"');
			expect(html).toContain("width:38%");

			// Stepper pills 00..02 completed, 03 active (aria-current="step"), 04..07 queued
			expect(html).toContain(
				'data-testid="progress-step-00" data-step-state="completed"',
			);
			expect(html).toContain(
				'data-testid="progress-step-02" data-step-state="completed"',
			);
			expect(html).toContain(
				'data-testid="progress-step-03" data-step-state="active" aria-current="step"',
			);
			expect(html).toContain(
				'data-testid="progress-step-04" data-step-state="queued"',
			);
			expect(html).toContain(
				'data-testid="progress-step-07" data-step-state="queued"',
			);
		});

		it("renders PAUSED ON HOLD state with amber badge and hold stepper indicator", () => {
			const stages = cloneStagesWithTags({ 2: "ASK-USER" });
			const html = renderToStaticMarkup(
				<PipelineRunProgressBar
					stages={stages}
					runState="hold"
					activeRunningStageIdx={2}
					statusBannerText="Hold · STAGE_02 awaiting steward"
				/>,
			);

			expect(html).toContain("PAUSED ON HOLD");
			expect(html).toContain("#B45309");
			expect(html).toContain("2 / 8 Stages Complete · 25%");
			expect(html).toContain('max="8"');
			expect(html).toContain('value="2"');
			expect(html).toContain("width:25%");
			expect(html).toContain(
				'data-testid="progress-step-00" data-step-state="completed"',
			);
			expect(html).toContain(
				'data-testid="progress-step-01" data-step-state="completed"',
			);
			expect(html).toContain(
				'data-testid="progress-step-02" data-step-state="hold"',
			);
			expect(html).toContain("⏸");
		});

		it("renders FAILED state with crimson badge and failed stepper indicator", () => {
			const stages = cloneStagesWithTags({ 5: "REJECT-WITH-TRACE" });
			const html = renderToStaticMarkup(
				<PipelineRunProgressBar
					stages={stages}
					runState="failed"
					activeRunningStageIdx={5}
					statusBannerText="Failed · STAGE_05 gate violation"
				/>,
			);

			expect(html).toContain("FAILED");
			expect(html).toContain("#B91C1C");
			expect(html).toContain("5 / 8 Stages Complete · 63%");
			expect(html).toContain('max="8"');
			expect(html).toContain('value="5"');
			expect(html).toContain("width:63%");
			expect(html).toContain(
				'data-testid="progress-step-04" data-step-state="completed"',
			);
			expect(html).toContain(
				'data-testid="progress-step-05" data-step-state="failed"',
			);
			expect(html).toContain("✕");
		});

		it("renders SEALED state with 8 / 8 Stages Complete · 100% and green badge", () => {
			const stages = GLOBAL_COMMERCE_EXPLORER_BUNDLE.stages;
			const html = renderToStaticMarkup(
				<PipelineRunProgressBar
					stages={stages}
					runState="sealed"
					activeRunningStageIdx={null}
					statusBannerText="Sealed · 6/6 gates PASS"
				/>,
			);

			expect(html).toContain("SEALED");
			expect(html).toContain("#15803D");
			expect(html).toContain("8 / 8 Stages Complete · 100%");
			expect(html).toContain('max="8"');
			expect(html).toContain('value="8"');
			expect(html).toContain("width:100%");

			for (let i = 0; i < 8; i++) {
				const numStr = String(i).padStart(2, "0");
				expect(html).toContain(
					`data-testid="progress-step-${numStr}" data-step-state="completed"`,
				);
			}
		});

		it("invokes onSelectStage with the clicked stage index on stepper pills", () => {
			const stages = cloneStagesWithTags();
			const selectedIndices: number[] = [];
			const tree = PipelineRunProgressBar({
				stages,
				runState: "running",
				activeRunningStageIdx: 1,
				statusBannerText: "Running",
				onSelectStage: (idx) => selectedIndices.push(idx),
			});

			// Find the stepper group div (3rd child of section)
			const sectionChildren = tree.props.children as React.ReactElement[];
			const stepperGroup = sectionChildren[2];
			const buttons = stepperGroup?.props.children as React.ReactElement<{
				onClick: () => void;
			}>[];
			expect(buttons).toHaveLength(8);

			buttons[0]?.props.onClick();
			buttons[4]?.props.onClick();
			buttons[7]?.props.onClick();

			expect(selectedIndices).toEqual([0, 4, 7]);
			expect(computeCompletedStagesCount(stages, "running", 1)).toBe(1);
			const secondStage = stages[1];
			expect(secondStage).toBeDefined();
			if (secondStage) {
				expect(getStepperPillState(secondStage, 1, "running", 1, 1)).toBe(
					"active",
				);
			}
		});
	});

	describe("2. OverviewStagesTrack & PipelineOverviewView Per-Stage Progress Indicators", () => {
		it("renders running, completed, and queued indicators on stage cards during an active run", () => {
			const html = renderToStaticMarkup(
				<OverviewStagesTrack
					phases={GLOBAL_COMMERCE_EXPLORER_BUNDLE.phases}
					stages={GLOBAL_COMMERCE_EXPLORER_BUNDLE.stages}
					artifacts={GLOBAL_COMMERCE_EXPLORER_BUNDLE.artifacts}
					activeWave="all"
					onOpenStage={() => {}}
					runState="running"
					activeRunningStageIdx={3}
				/>,
			);

			// Stages 00..02 are completed
			expect(html).toContain(
				'data-testid="stage-card-00" data-run-status="completed"',
			);
			expect(html).toContain('data-testid="stage-completed-indicator-00"');
			expect(html).toContain("✓ COMPLETED");
			expect(html).toContain(
				'data-testid="stage-card-02" data-run-status="completed"',
			);
			expect(html).toContain('data-testid="stage-completed-indicator-02"');

			// Stage 03 is actively running
			expect(html).toContain(
				'data-testid="stage-card-03" data-run-status="running"',
			);
			expect(html).toContain('data-testid="stage-running-indicator-03"');
			expect(html).toContain("● RUNNING");
			expect(html).toContain("2px solid #1D4ED8");
			expect(html).toContain("0 0 0 3px rgba(29, 78, 216, 0.12)");

			// Stages 04..07 are queued with muted opacity
			expect(html).toContain(
				'data-testid="stage-card-04" data-run-status="queued"',
			);
			expect(html).toContain('data-testid="stage-queued-indicator-04"');
			expect(html).toContain("◌ QUEUED");
			expect(html).toContain("opacity:0.78");
			expect(html).toContain(
				'data-testid="stage-card-07" data-run-status="queued"',
			);
		});

		it("forwards runState and activeRunningStageIdx from PipelineOverviewView to OverviewStagesTrack", () => {
			const html = renderToStaticMarkup(
				<PipelineOverviewView
					settingIds={GLOBAL_COMMERCE_EXPLORER_BUNDLE.settingIds}
					inputIds={GLOBAL_COMMERCE_EXPLORER_BUNDLE.inputIds}
					outputIds={GLOBAL_COMMERCE_EXPLORER_BUNDLE.outputIds}
					artifacts={GLOBAL_COMMERCE_EXPLORER_BUNDLE.artifacts}
					phases={GLOBAL_COMMERCE_EXPLORER_BUNDLE.phases}
					stages={GLOBAL_COMMERCE_EXPLORER_BUNDLE.stages}
					hasInitialContract={
						GLOBAL_COMMERCE_EXPLORER_BUNDLE.hasInitialContract
					}
					activeWave="all"
					selType={null}
					selId={null}
					onSelectArtifact={() => {}}
					onOpenFullArtifact={() => {}}
					onOpenStage={() => {}}
					onClearSelection={() => {}}
					runState="running"
					activeRunningStageIdx={1}
				/>,
			);

			expect(html).toContain(
				'data-testid="stage-card-00" data-run-status="completed"',
			);
			expect(html).toContain(
				'data-testid="stage-card-01" data-run-status="running"',
			);
			expect(html).toContain('data-testid="stage-running-indicator-01"');
			expect(html).toContain(
				'data-testid="stage-card-02" data-run-status="queued"',
			);
		});

		it("handles empty stages array, sealed state, and OverviewStagesTrack sealed/hold/failed states", () => {
			expect(computeCompletedStagesCount([], "running", 0)).toBe(0);
			const emptyHtml = renderToStaticMarkup(
				<PipelineRunProgressBar
					stages={[]}
					runState="running"
					activeRunningStageIdx={null}
					statusBannerText="Idle"
				/>,
			);
			expect(emptyHtml).toContain("0 / 0 Stages Complete · 0%");

			// Sealed run overrides per-stage ASK-USER tag in getStepperPillState
			const firstStage = GLOBAL_COMMERCE_EXPLORER_BUNDLE.stages[0];
			if (!firstStage) throw new Error("Expected at least one stage");
			const stageWithHoldTag = {
				...firstStage,
				tag: "ASK-USER" as const,
			};
			expect(getStepperPillState(stageWithHoldTag, 0, "sealed", null, 8)).toBe(
				"completed",
			);

			// OverviewStagesTrack in sealed state renders completed indicators
			const sealedTrackHtml = renderToStaticMarkup(
				<OverviewStagesTrack
					phases={GLOBAL_COMMERCE_EXPLORER_BUNDLE.phases}
					stages={GLOBAL_COMMERCE_EXPLORER_BUNDLE.stages}
					artifacts={GLOBAL_COMMERCE_EXPLORER_BUNDLE.artifacts}
					activeWave="all"
					onOpenStage={() => {}}
					runState="sealed"
					activeRunningStageIdx={null}
				/>,
			);
			expect(sealedTrackHtml).toContain(
				'data-testid="stage-card-00" data-run-status="completed"',
			);
			expect(sealedTrackHtml).toContain(
				'data-testid="stage-completed-indicator-00"',
			);

			// OverviewStagesTrack in hold/failed states renders idle run-status for non-running stages
			const holdTrackHtml = renderToStaticMarkup(
				<OverviewStagesTrack
					phases={GLOBAL_COMMERCE_EXPLORER_BUNDLE.phases}
					stages={GLOBAL_COMMERCE_EXPLORER_BUNDLE.stages}
					artifacts={GLOBAL_COMMERCE_EXPLORER_BUNDLE.artifacts}
					activeWave="all"
					onOpenStage={() => {}}
					runState="hold"
					activeRunningStageIdx={2}
				/>,
			);
			expect(holdTrackHtml).toContain(
				'data-testid="stage-card-02" data-run-status="idle"',
			);

			// Verify keyboard navigation on stage cards (Enter, Space, ArrowDown, ArrowUp)
			const openedStages: number[] = [];
			const trackTree = OverviewStagesTrack({
				phases: GLOBAL_COMMERCE_EXPLORER_BUNDLE.phases,
				stages: GLOBAL_COMMERCE_EXPLORER_BUNDLE.stages,
				artifacts: GLOBAL_COMMERCE_EXPLORER_BUNDLE.artifacts,
				activeWave: "all",
				onOpenStage: (idx) => openedStages.push(idx),
				runState: "running",
				activeRunningStageIdx: 1,
			});

			const findStageCard = (
				node: unknown,
				testId: string,
			): React.ReactElement<{
				"data-testid"?: string;
				onKeyDown: (ev: {
					key: string;
					preventDefault: () => void;
					currentTarget: {
						closest: () => {
							querySelectorAll: () => Array<{ focus: () => void }>;
						};
					};
				}) => void;
			}> | null => {
				if (!node || typeof node !== "object") return null;
				if (Array.isArray(node)) {
					for (const item of node) {
						const found = findStageCard(item, testId);
						if (found) return found;
					}
					return null;
				}
				const el = node as React.ReactElement<{
					"data-testid"?: string;
					children?: unknown;
				}>;
				if (el.props?.["data-testid"] === testId) {
					return el as unknown as ReturnType<typeof findStageCard>;
				}
				return findStageCard(el.props?.children, testId);
			};

			const card01 = findStageCard(trackTree, "stage-card-01");
			expect(card01).not.toBeNull();
			const focusedIdx: number[] = [];
			const mockCards = [0, 1, 2].map((i) => ({
				focus: () => focusedIdx.push(i),
			}));
			const currentTarget = mockCards[1] as unknown as {
				closest: () => { querySelectorAll: () => Array<{ focus: () => void }> };
			};
			currentTarget.closest = () => ({
				querySelectorAll: () => mockCards,
			});

			card01?.props.onKeyDown({
				key: "Enter",
				preventDefault: () => {},
				currentTarget,
			});
			expect(openedStages).toEqual([1]);

			card01?.props.onKeyDown({
				key: "ArrowDown",
				preventDefault: () => {},
				currentTarget,
			});
			expect(focusedIdx).toEqual([2]);

			card01?.props.onKeyDown({
				key: "ArrowUp",
				preventDefault: () => {},
				currentTarget,
			});
			expect(focusedIdx).toEqual([2, 0]);
		});
	});

	describe("3. Static Asset Builder (build-static.ts)", () => {
		it("generates production index.html and bundles workbench.js + workbench.css", async () => {
			const html = generateStaticIndexHtml();
			expect(html).toContain('<div id="root"></div>');
			expect(html).toContain(
				'<link rel="stylesheet" href="/assets/workbench.css" />',
			);
			expect(html).toContain(
				'<script type="module" src="/assets/workbench.js"></script>',
			);

			const tempDir = mkdtempSync(join(tmpdir(), "opendv-static-test-"));
			try {
				const result = await buildStaticWorkbenchAssets({
					outDirs: [tempDir],
					silent: true,
				});
				expect(result.jsBytes).toBeGreaterThan(1000);
				expect(result.cssBytes).toBeGreaterThan(100);
				expect(existsSync(join(tempDir, "index.html"))).toBe(true);
				expect(existsSync(join(tempDir, "assets", "workbench.js"))).toBe(true);
				expect(existsSync(join(tempDir, "assets", "workbench.css"))).toBe(true);
				expect(readFileSync(join(tempDir, "index.html"), "utf-8")).toBe(html);
			} finally {
				rmSync(tempDir, { recursive: true, force: true });
			}
		});
	});
});
