/**
 * Responsive 3-Layer Progressive Disclosure Workbench Shell (`WorkbenchShell.tsx`) (`docs/06` §2.2 & `docs/10` §3.2).
 *
 * Orchestrates:
 * - Layer 1 (`Layer1StoryBanner`): 5-second executive scan bar (`aria-live="polite"`)
 * - Left Rail (`SidebarNav`): Collapsible 240px Scene/View navigation rail (`WB-00`–`WB-11`)
 * - Layer 2 (`Center Interactive Workbench Canvas`): Active `WorkbenchViewPlugin` + Semantic Hash Pills + Co-Located Gate Cards
 * - Layer 3 (`Layer3ProofDrawer`): Native `<dialog>` Formal Proof Drawer with `inert` background management
 * - Global Keyboard Shortcuts: `Space`/`K` (Play/Pause), `L`/`ArrowRight` (Step), `P` (Proof Drawer), `Esc` (Close), `1`–`9`,`0`,`-`,`=` (`WB-00`–`WB-11`)
 */

import {
	WORKBENCH_VIEW_STAGE_MAP,
	type WorkbenchViewId,
} from "@opendv/protocol-ts";
import { useEffect, useState } from "react";
import { WorkbenchPluginRegistry } from "../../plugins/plugin-registry";
import { useWorkbenchStore } from "../../store/workbench-store";
import {
	ENTITY_VISUAL_GRAMMAR,
	VERDICT_VISUAL_GRAMMAR,
} from "../../theme/theme-registry";
import { Layer1StoryBanner } from "./Layer1StoryBanner";
import {
	DEFAULT_BACKGROUND_WORKSPACE_ID,
	Layer3ProofDrawer,
} from "./Layer3ProofDrawer";
import { SHORTCUT_KEY_TO_VIEW_ID, SidebarNav } from "./SidebarNav";

export interface WorkbenchKeyboardActions {
	togglePlayPause: () => void;
	stepForward: () => void;
	toggleProofDrawer: () => void;
	closeProofDrawer: () => void;
	setActiveView: (viewId: WorkbenchViewId) => void;
}

/**
 * Pure keyboard shortcut router for the 12-View Explainability Workbench (`docs/10` §3.2).
 * Returns `true` if the key matched a registered workbench shortcut, `false` otherwise.
 */
export function routeWorkbenchKeyboardShortcut(
	key: string,
	actions: WorkbenchKeyboardActions,
): boolean {
	if (key === " " || key === "k" || key === "K") {
		actions.togglePlayPause();
		return true;
	}
	if (key === "l" || key === "L" || key === "ArrowRight") {
		actions.stepForward();
		return true;
	}
	if (key === "p" || key === "P") {
		actions.toggleProofDrawer();
		return true;
	}
	if (key === "Escape") {
		actions.closeProofDrawer();
		return true;
	}
	const mappedViewId = SHORTCUT_KEY_TO_VIEW_ID[key];
	if (mappedViewId) {
		actions.setActiveView(mappedViewId);
		return true;
	}
	return false;
}

const INTERACTIVE_TAGS = new Set([
	"INPUT",
	"TEXTAREA",
	"SELECT",
	"OPTION",
	"BUTTON",
	"A",
	"SUMMARY",
]);

const INTERACTIVE_ROLES = new Set([
	"button",
	"link",
	"tab",
	"menuitem",
	"option",
	"switch",
	"checkbox",
	"radio",
	"slider",
	"spinbutton",
	"textbox",
	"combobox",
	"searchbox",
]);

const INTERACTIVE_SELECTOR =
	"input,textarea,select,option,button,a,summary,[contenteditable='true'],[contenteditable=''],[role='button'],[role='link'],[role='tab'],[role='menuitem'],[role='option'],[role='switch'],[role='checkbox'],[role='radio'],[role='slider'],[role='spinbutton'],[role='textbox'],[role='combobox'],[role='searchbox']";

interface InteractiveElementLike {
	tagName?: string;
	isContentEditable?: boolean;
	role?: string | null;
	getAttribute?: (name: string) => string | null;
	closest?: (selector: string) => unknown;
	parentElement?: InteractiveElementLike | null;
}

export function isInteractiveInputTarget(target: EventTarget | null): boolean {
	if (!target || typeof target !== "object") {
		return false;
	}
	const root = target as InteractiveElementLike;
	if (
		typeof root.closest === "function" &&
		root.closest(INTERACTIVE_SELECTOR)
	) {
		return true;
	}
	let current: InteractiveElementLike | null = root;
	while (current) {
		if (current.isContentEditable) {
			return true;
		}
		const contentEditableAttr = current.getAttribute?.("contenteditable");
		if (contentEditableAttr === "" || contentEditableAttr === "true") {
			return true;
		}
		const tag = current.tagName?.toUpperCase();
		if (tag && INTERACTIVE_TAGS.has(tag)) {
			return true;
		}
		const role = (
			current.getAttribute?.("role") ??
			current.role ??
			""
		).toLowerCase();
		if (role && INTERACTIVE_ROLES.has(role)) {
			return true;
		}
		current = current.parentElement ?? null;
	}
	return false;
}

export interface WorkbenchShellProps {
	pluginRegistry?: WorkbenchPluginRegistry | undefined;
}

export function WorkbenchShell({ pluginRegistry }: WorkbenchShellProps) {
	const activeViewId = useWorkbenchStore((s) => s.activeViewId);
	const currentEvent = useWorkbenchStore((s) => s.currentEvent);
	const eventsByView = useWorkbenchStore((s) => s.eventsByView);
	const isProofDrawerOpen = useWorkbenchStore((s) => s.isProofDrawerOpen);
	const openProofDrawer = useWorkbenchStore((s) => s.openProofDrawer);
	const toggleProofDrawer = useWorkbenchStore((s) => s.toggleProofDrawer);
	const closeProofDrawer = useWorkbenchStore((s) => s.closeProofDrawer);
	const togglePlayPause = useWorkbenchStore((s) => s.togglePlayPause);
	const stepForward = useWorkbenchStore((s) => s.stepForward);
	const setActiveView = useWorkbenchStore((s) => s.setActiveView);

	const [expandedGateIds, setExpandedGateIds] = useState<
		Record<string, boolean>
	>({});
	const [, setPluginRevision] = useState(0);

	useEffect(() => {
		const subscribeFn = pluginRegistry
			? pluginRegistry.subscribe.bind(pluginRegistry)
			: WorkbenchPluginRegistry.subscribe;
		return subscribeFn(() => {
			setPluginRevision((rev) => rev + 1);
		});
	}, [pluginRegistry]);

	useEffect(() => {
		if (
			typeof globalThis === "undefined" ||
			typeof globalThis.addEventListener !== "function"
		) {
			return;
		}
		const onKeyDown = (ev: KeyboardEvent) => {
			if (ev.metaKey || ev.ctrlKey || ev.altKey) {
				return;
			}
			if (ev.key !== "Escape" && isInteractiveInputTarget(ev.target)) {
				return;
			}
			const handled = routeWorkbenchKeyboardShortcut(ev.key, {
				togglePlayPause,
				stepForward: () => {
					void stepForward();
				},
				toggleProofDrawer,
				closeProofDrawer,
				setActiveView,
			});
			if (handled) {
				ev.preventDefault();
			}
		};
		globalThis.addEventListener("keydown", onKeyDown);
		return () => {
			globalThis.removeEventListener("keydown", onKeyDown);
		};
	}, [
		togglePlayPause,
		stepForward,
		toggleProofDrawer,
		closeProofDrawer,
		setActiveView,
	]);

	const activeViewMeta = WORKBENCH_VIEW_STAGE_MAP[activeViewId];
	const registeredViewPlugin = pluginRegistry
		? pluginRegistry.getView(activeViewId)
		: WorkbenchPluginRegistry.getView(activeViewId);
	const eventsForActiveView = eventsByView[activeViewId] ?? [];

	const matchingGateCards = currentEvent
		? pluginRegistry
			? pluginRegistry.findGateCardsForEvent(currentEvent)
			: WorkbenchPluginRegistry.findGateCardsForEvent(currentEvent)
		: [];

	const ActiveViewComponent = registeredViewPlugin?.component;
	const pills = currentEvent?.layer2_visual_payload.semantic_hash_pills ?? [];
	const summaryMetrics =
		currentEvent?.layer2_visual_payload.summary_metrics ?? {};
	const verdictMeta = currentEvent
		? VERDICT_VISUAL_GRAMMAR[currentEvent.status]
		: VERDICT_VISUAL_GRAMMAR.PASS;

	const toggleGateExpanded = (gateId: string) => {
		setExpandedGateIds((prev) => ({
			...prev,
			[gateId]: !(prev[gateId] ?? true),
		}));
	};

	return (
		<div
			data-testid="workbench-shell"
			style={{
				minHeight: "100vh",
				display: "flex",
				flexDirection: "column",
				backgroundColor: "var(--dv-bg-canvas)",
				color: "var(--dv-text-primary)",
			}}
		>
			{/* Layer 1: Executive Story Banner */}
			<Layer1StoryBanner />

			{/* Main Workspace (marked inert when Layer 3 modal Proof Drawer is open) */}
			<div
				id={DEFAULT_BACKGROUND_WORKSPACE_ID}
				data-testid="workbench-main-workspace"
				inert={isProofDrawerOpen ? true : undefined}
				style={{
					flex: 1,
					display: "flex",
					minHeight: "calc(100vh - 56px)",
				}}
			>
				{/* Left Collapsible Scene/View Rail */}
				<SidebarNav pluginRegistry={pluginRegistry} />

				{/* Layer 2: Interactive Visual Canvas */}
				<main
					data-layer="layer-2-visual-canvas"
					data-testid="layer2-visual-canvas"
					data-active-view={activeViewId}
					style={{
						flex: 1,
						padding: "1.25rem",
						overflowY: "auto",
						display: "flex",
						flexDirection: "column",
						gap: "1rem",
					}}
				>
					{/* View Header Bar */}
					<div
						style={{
							display: "flex",
							flexWrap: "wrap",
							alignItems: "center",
							justifyContent: "space-between",
							gap: "0.75rem",
							paddingBottom: "0.75rem",
							borderBottom: "1px solid var(--dv-border-subtle)",
						}}
					>
						<div>
							<div
								style={{
									display: "flex",
									alignItems: "center",
									gap: "0.5rem",
									fontSize: "0.75rem",
									color: "var(--dv-text-secondary)",
								}}
							>
								<code>{activeViewId}</code>
								<span>·</span>
								<span>
									Scene{" "}
									{registeredViewPlugin?.sceneNumber ??
										activeViewMeta.sceneNumber}
								</span>
								<span>·</span>
								<code>
									{registeredViewPlugin?.stageId ?? activeViewMeta.stageId}
								</code>
								<span>·</span>
								<code>{activeViewMeta.primaryVizId}</code>
							</div>
							<h1
								style={{
									margin: "0.25rem 0 0",
									fontSize: "1.125rem",
									fontWeight: 700,
								}}
							>
								{registeredViewPlugin?.title ?? activeViewMeta.title}
							</h1>
						</div>

						{/* WCAG AA Dual-Encoded Entity Grammar Legend */}
						<div
							aria-label="WCAG AA Dual-Encoded Entity Legend"
							style={{
								display: "flex",
								flexWrap: "wrap",
								gap: "0.375rem",
							}}
						>
							{Object.values(ENTITY_VISUAL_GRAMMAR).map((entity) => (
								<span
									key={entity.kind}
									style={{
										fontSize: "0.68rem",
										fontWeight: 600,
										padding: "0.15rem 0.4rem",
										borderRadius: entity.kind === "SAT" ? "9999px" : "0.25rem",
										border: `${entity.borderStyle} var(${entity.cssColorVar})`,
										color: `var(${entity.cssColorVar})`,
										backgroundColor: "var(--dv-surface-1)",
									}}
								>
									{entity.prefixBadge}
								</span>
							))}
						</div>
					</div>

					{/* Active Registered View Plugin or Default Layer 2 Progressive Disclosure Canvas */}
					{ActiveViewComponent ? (
						<section
							aria-label={`${activeViewId} Registered View Plugin Canvas`}
							data-testid="registered-view-plugin-container"
						>
							<ActiveViewComponent
								currentEvent={currentEvent}
								eventsForView={eventsForActiveView}
								onInspectProof={() => openProofDrawer("3A")}
							/>
						</section>
					) : (
						<section
							aria-label={`${activeViewId} Layer 2 Interactive Canvas`}
							data-testid="default-layer2-canvas"
							style={{
								display: "flex",
								flexDirection: "column",
								gap: "1rem",
							}}
						>
							{/* Semantic Hash Pills Section */}
							<div
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
										marginBottom: "0.5rem",
									}}
								>
									<h2 style={{ margin: 0, fontSize: "0.875rem" }}>
										Layer 2 Semantic Hash Pills (Zero 64-Hex String Fatigue)
									</h2>
									<span
										style={{
											fontSize: "0.75rem",
											fontWeight: 700,
											color: `var(${verdictMeta.cssColorVar})`,
										}}
									>
										{verdictMeta.shapeSymbol} {verdictMeta.prefixBadge}
									</span>
								</div>

								<div
									data-testid="layer2-hash-pills"
									style={{
										display: "flex",
										flexWrap: "wrap",
										gap: "0.5rem",
									}}
								>
									{pills.length > 0 ? (
										pills.map((pill) => (
											<button
												key={`${pill.full_sha256_hex}-${pill.business_key}`}
												type="button"
												title={`Full SHA-256: ${pill.full_sha256_hex} (Click to inspect in Layer 3C)`}
												data-testid="semantic-hash-pill"
												onClick={() => openProofDrawer("3C")}
												style={{
													backgroundColor: "var(--dv-surface-2)",
													color: "var(--dv-text-primary)",
													border: "1.5px solid var(--dv-hub-accent)",
													borderRadius: "9999px",
													padding: "0.25rem 0.65rem",
													fontFamily:
														"ui-monospace, SFMono-Regular, Menlo, monospace",
													fontSize: "0.75rem",
													cursor: "pointer",
												}}
											>
												{pill.pill_label}
											</button>
										))
									) : (
										<span
											style={{
												fontSize: "0.75rem",
												color: "var(--dv-text-muted)",
											}}
										>
											No semantic hash pills in current telemetry frame.
										</span>
									)}
								</div>
							</div>

							{/* Summary Metrics Grid */}
							<div
								data-testid="layer2-summary-metrics"
								style={{
									display: "grid",
									gridTemplateColumns: "repeat(auto-fill, minmax(180px, 1fr))",
									gap: "0.75rem",
								}}
							>
								{Object.entries(summaryMetrics).map(
									([metricKey, metricVal]) => (
										<div
											key={metricKey}
											style={{
												backgroundColor: "var(--dv-surface-1)",
												border: "1px solid var(--dv-border-subtle)",
												borderRadius: "0.5rem",
												padding: "0.75rem",
											}}
										>
											<div
												style={{
													fontSize: "0.7rem",
													color: "var(--dv-text-secondary)",
												}}
											>
												{metricKey}
											</div>
											<div
												style={{
													fontSize: "1.1rem",
													fontWeight: 700,
													marginTop: "0.25rem",
												}}
											>
												{String(metricVal)}
											</div>
										</div>
									),
								)}
							</div>
						</section>
					)}

					{/* Co-Located Gate Card Plugins */}
					{currentEvent && matchingGateCards.length > 0 && (
						<section
							aria-label="Co-Located Verification Gate Cards"
							data-testid="colocated-gate-cards"
							style={{
								display: "flex",
								flexDirection: "column",
								gap: "0.75rem",
							}}
						>
							{matchingGateCards.map((gatePlugin) => {
								const GateComponent = gatePlugin.component;
								const isExpanded = expandedGateIds[gatePlugin.gateId] ?? true;
								return (
									<GateComponent
										key={gatePlugin.gateId}
										event={currentEvent}
										isExpanded={isExpanded}
										onToggleExpand={() => toggleGateExpanded(gatePlugin.gateId)}
									/>
								);
							})}
						</section>
					)}
				</main>
			</div>

			{/* Layer 3: Native <dialog> Formal Proof Drawer */}
			<Layer3ProofDrawer />
		</div>
	);
}
