/**
 * M5 Failing-First Regression Tests — M4 Wizard & Store Caveats (S1, S2, S3)
 * (`apps/opendv-web-workbench/tests/m5-wizard-caveats.test.tsx`)
 *
 * Covers:
 * - S1: `Step5DiffGraphExport` live `window` `resize` listener toggling `< 768px` view-only mode (`D26`)
 * - S2: `loadPipelineBaselines` in-flight async race guard preserving user selection while retaining `UnifiedWizard.test.tsx:1507` reload semantics
 * - S3: `Step1ScopeAndBaseline` `flexWrap: "wrap"` on Option A header, Option B header, and Selected Baseline Digest card (`< 390px` pre-font-load layout)
 */

import { describe, expect, it } from "bun:test";
import * as React from "react";
import { createStudioStore } from "../src/ontology/store/studio-store";
import { Step1ScopeAndBaseline } from "../src/ontology/wizard/Step1ScopeAndBaseline";
import { Step5DiffGraphExport } from "../src/ontology/wizard/Step5DiffGraphExport";

interface ReactElementNode {
	type?: unknown;
	props?: Record<string, unknown>;
}

function collectText(node: unknown): string {
	if (node === null || node === undefined || typeof node === "boolean") {
		return "";
	}
	if (typeof node === "string" || typeof node === "number") {
		return String(node);
	}
	if (Array.isArray(node)) {
		return node.map(collectText).join("");
	}
	if (typeof node === "object") {
		const el = node as ReactElementNode;
		return el.props?.children ? collectText(el.props.children) : "";
	}
	return "";
}

function findElements(
	node: unknown,
	predicate: (el: ReactElementNode) => boolean,
): ReactElementNode[] {
	const matches: ReactElementNode[] = [];
	const walk = (cur: unknown) => {
		if (!cur || typeof cur !== "object") return;
		if (Array.isArray(cur)) {
			for (const item of cur) walk(item);
			return;
		}
		const el = cur as ReactElementNode;
		if (el.props) {
			if (predicate(el)) matches.push(el);
			if (el.props.children !== undefined) walk(el.props.children);
		}
	};
	walk(node);
	return matches;
}

describe("M5 Wizard & Store Caveat Regressions (S1, S2, S3)", () => {
	it("S1: updates Step5DiffGraphExport view-only state live on window resize events across 768px", () => {
		const internals = (
			React as unknown as {
				__CLIENT_INTERNALS_DO_NOT_USE_OR_WARN_USERS_THEY_CANNOT_UPGRADE?: {
					H?: unknown;
				};
			}
		).__CLIENT_INTERNALS_DO_NOT_USE_OR_WARN_USERS_THEY_CANNOT_UPGRADE;
		expect(internals).toBeDefined();
		if (!internals) return;

		const resizeListeners = new Set<(ev: Event) => void>();
		const mockWindow = {
			innerWidth: 1280,
			addEventListener: (type: string, cb: (ev: Event) => void) => {
				if (type === "resize") resizeListeners.add(cb);
			},
			removeEventListener: (type: string, cb: (ev: Event) => void) => {
				if (type === "resize") resizeListeners.delete(cb);
			},
			dispatchEvent: (ev: Event) => {
				if (ev.type === "resize") {
					for (const listener of [...resizeListeners]) {
						listener(ev);
					}
				}
				return true;
			},
		};

		const globalRec = globalThis as Record<string, unknown>;
		const hadWindow = Object.prototype.hasOwnProperty.call(globalRec, "window");
		const prevWindow = globalRec.window;
		const prevH = internals.H;

		const stateSlots: unknown[] = [];
		const refSlots: Array<{ current: unknown }> = [];
		const effectSlots: Array<{
			deps: readonly unknown[] | undefined;
			cleanup: (() => void) | undefined;
		}> = [];
		let pendingEffects: Array<{
			slot: number;
			effect: React.EffectCallback;
			deps: readonly unknown[] | undefined;
		}> = [];
		let stateIdx = 0;
		let refIdx = 0;
		let effectIdx = 0;
		let rerenderScheduled = false;

		const depsEqual = (
			a: readonly unknown[] | undefined,
			b: readonly unknown[] | undefined,
		): boolean => {
			if (!a || !b || a.length !== b.length) return false;
			return a.every((val, i) => Object.is(val, b[i]));
		};

		const dispatcher = {
			useState<T>(initial: T | (() => T)) {
				const idx = stateIdx++;
				if (!(idx in stateSlots)) {
					stateSlots[idx] =
						typeof initial === "function" ? (initial as () => T)() : initial;
				}
				const setState = (val: T | ((prev: T) => T)) => {
					const next =
						typeof val === "function"
							? (val as (prev: T) => T)(stateSlots[idx] as T)
							: val;
					if (!Object.is(stateSlots[idx], next)) {
						stateSlots[idx] = next;
						rerenderScheduled = true;
					}
				};
				return [stateSlots[idx] as T, setState];
			},
			useEffect(effect: React.EffectCallback, deps?: readonly unknown[]) {
				const slot = effectIdx++;
				const prev = effectSlots[slot];
				if (!prev || !depsEqual(prev.deps, deps)) {
					pendingEffects.push({ slot, effect, deps });
				}
			},
			useRef<T>(initial: T) {
				const idx = refIdx++;
				if (!(idx in refSlots)) {
					refSlots[idx] = { current: initial };
				}
				return refSlots[idx] as { current: T };
			},
			useMemo<T>(factory: () => T) {
				return factory();
			},
			useCallback<T>(fn: T) {
				return fn;
			},
		};

		const renderComponent = (): unknown => {
			let tree: unknown = null;
			for (let guard = 0; guard < 10; guard += 1) {
				stateIdx = 0;
				refIdx = 0;
				effectIdx = 0;
				pendingEffects = [];
				rerenderScheduled = false;
				tree = Step5DiffGraphExport({
					sessionId: "ses-s1-resize",
					diffSummary: {
						fiboAdded: 1,
						customAdded: 0,
						enrichedCount: 0,
						linksAdded: 1,
						validationPassed: true,
					},
				});
				const toRun = [...pendingEffects];
				pendingEffects = [];
				for (const item of toRun) {
					effectSlots[item.slot]?.cleanup?.();
					const cleanup = item.effect();
					effectSlots[item.slot] = {
						deps: item.deps,
						cleanup: typeof cleanup === "function" ? cleanup : undefined,
					};
				}
				if (!rerenderScheduled) break;
			}
			return tree;
		};

		globalRec.window = mockWindow;
		internals.H = dispatcher;
		try {
			// 1. Initial mount at desktop 1280px (no explicit viewportWidth prop)
			let tree = renderComponent();
			expect(collectText(tree)).not.toContain("View-only");
			const desktopBtn = findElements(
				tree,
				(el) => el.props?.["data-testid"] === "btn-open-visual-editor",
			)[0];
			expect(desktopBtn).toBeDefined();
			expect(Boolean(desktopBtn?.props?.disabled)).toBe(false);
			expect(
				findElements(
					tree,
					(el) =>
						typeof el.props?.className === "string" &&
						el.props.className.includes("view-only-mobile"),
				),
			).toHaveLength(0);

			// 2. Resize window below 768px — must trigger state update & re-render via resize listener
			mockWindow.innerWidth = 390;
			mockWindow.dispatchEvent(new Event("resize"));
			if (rerenderScheduled) {
				tree = renderComponent();
			}

			expect(collectText(tree)).toContain("View-only");
			const mobileBtn = findElements(
				tree,
				(el) => el.props?.["data-testid"] === "btn-open-visual-editor",
			)[0];
			expect(mobileBtn?.props?.disabled).toBe(true);
			expect(
				findElements(
					tree,
					(el) =>
						typeof el.props?.className === "string" &&
						el.props.className.includes("view-only-mobile"),
				),
			).toHaveLength(1);

			// 3. Resize back to desktop 1024px — view-only banner & disabled state must clear
			mockWindow.innerWidth = 1024;
			mockWindow.dispatchEvent(new Event("resize"));
			if (rerenderScheduled) {
				tree = renderComponent();
			}

			expect(collectText(tree)).not.toContain("View-only");
			const restoredBtn = findElements(
				tree,
				(el) => el.props?.["data-testid"] === "btn-open-visual-editor",
			)[0];
			expect(Boolean(restoredBtn?.props?.disabled)).toBe(false);
			expect(
				findElements(
					tree,
					(el) =>
						typeof el.props?.className === "string" &&
						el.props.className.includes("view-only-mobile"),
				),
			).toHaveLength(0);

			// 4. Unmount runs effect cleanup without leaking listeners
			for (const slot of effectSlots) {
				slot.cleanup?.();
			}
			expect(resizeListeners.size).toBe(0);
		} finally {
			internals.H = prevH;
			if (hadWindow) {
				globalRec.window = prevWindow;
			} else {
				Reflect.deleteProperty(globalRec, "window");
			}
		}
	});

	it("S2: preserves in-flight baseline selection during loadPipelineBaselines while keeping pre-fetch reload behavior", async () => {
		const store = createStudioStore();

		// Case 1: User selects __cold_start__ while /api/v1/runs is in flight
		let resolveRuns1!: (res: Response) => void;
		const deferredFetch1 = (() =>
			new Promise<Response>((resolve) => {
				resolveRuns1 = resolve;
			})) as unknown as typeof fetch;

		const loadPromise1 = store.getState().loadPipelineBaselines(deferredFetch1);
		store.getState().setSelectedBaselineRunId("__cold_start__");

		resolveRuns1(
			new Response(
				JSON.stringify([{ run_id: "run-cert-99", status: "COMPLETED" }]),
				{ status: 200 },
			),
		);
		await loadPromise1;

		expect(
			store
				.getState()
				.baselineOptions.some((opt) => opt.run_id === "run-cert-99"),
		).toBe(true);
		expect(store.getState().selectedBaselineRunId).toBe("__cold_start__");

		// Case 2: User uploads custom baseline contract while /api/v1/runs is in flight
		let resolveRuns2!: (res: Response) => void;
		const deferredFetch2 = (() =>
			new Promise<Response>((resolve) => {
				resolveRuns2 = resolve;
			})) as unknown as typeof fetch;

		const loadPromise2 = store.getState().loadPipelineBaselines(deferredFetch2);
		store
			.getState()
			.setSelectedBaselineRunId("__uploaded__", "model_name: InFlightUpload\n");

		resolveRuns2(
			new Response(
				JSON.stringify([{ run_id: "run-cert-100", status: "COMPLETED" }]),
				{ status: 200 },
			),
		);
		await loadPromise2;

		expect(
			store
				.getState()
				.baselineOptions.some((opt) => opt.run_id === "run-cert-100"),
		).toBe(true);
		expect(store.getState().selectedBaselineRunId).toBe("__uploaded__");
		expect(store.getState().customBaselineContractContent).toBe(
			"model_name: InFlightUpload\n",
		);

		// Case 3 (Companion — UnifiedWizard.test.tsx:1507): calling loadPipelineBaselines
		// AFTER a prior selection (no in-flight change) still selects latest certified & clears upload
		store
			.getState()
			.setSelectedBaselineRunId("__uploaded__", "model_name: ManualUpload");
		const immediateFetch = (async () =>
			new Response(
				JSON.stringify([{ run_id: "run-cert-99", status: "COMPLETED" }]),
				{ status: 200 },
			)) as unknown as typeof fetch;

		await store.getState().loadPipelineBaselines(immediateFetch);
		expect(store.getState().selectedBaselineRunId).toBe("run-cert-99");
		expect(store.getState().customBaselineContractContent).toBeNull();
	});

	it("S3: applies flexWrap: 'wrap' to Step1ScopeAndBaseline Option A header, Option B header, and Selected Baseline Digest row", () => {
		const tree = Step1ScopeAndBaseline({
			baselineMode: "new",
			activeDigest: {
				sessionId: "ses-s3-wrap",
				version: "v0.0.0",
				sha256: "a".repeat(64),
				label: "Blank Genesis Baseline",
			},
			onContinue: () => {},
		});

		const findFlexHeaderForHeading = (
			headingSubstring: string,
		): ReactElementNode | undefined => {
			const divs = findElements(tree, (el) => el.type === "div");
			return divs.find((div) => {
				const children = Array.isArray(div.props?.children)
					? div.props.children
					: [div.props?.children];
				return children.some(
					(child) =>
						Boolean(child) &&
						typeof child === "object" &&
						(child as ReactElementNode).type === "h2" &&
						collectText(child).includes(headingSubstring),
				);
			});
		};

		const optionAHeader = findFlexHeaderForHeading(
			"Option A · Start New Baseline",
		);
		const optionBHeader = findFlexHeaderForHeading(
			"Option B · Evolve Existing / Import",
		);
		const digestCard = findElements(
			tree,
			(el) => el.props?.["data-testid"] === "selected-baseline-digest-card",
		)[0];

		expect(optionAHeader).toBeDefined();
		expect(
			(optionAHeader?.props?.style as React.CSSProperties | undefined)
				?.flexWrap,
		).toBe("wrap");

		expect(optionBHeader).toBeDefined();
		expect(
			(optionBHeader?.props?.style as React.CSSProperties | undefined)
				?.flexWrap,
		).toBe("wrap");

		expect(digestCard).toBeDefined();
		expect(
			(digestCard?.props?.style as React.CSSProperties | undefined)?.flexWrap,
		).toBe("wrap");
	});
});
