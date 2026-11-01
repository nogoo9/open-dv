/**
 * Collapsible 240px Scene/View Navigation Rail (`SidebarNav.tsx`) (`docs/10` §3.2).
 *
 * Renders all 12 Workbench Views (`WB-00` through `WB-11`) with Scene grouping,
 * live event counters, WCAG AA verdict badges, and keyboard shortcut hints (`1`–`9`, `0`, `-`, `=`).
 */

import {
	ALL_WORKBENCH_VIEW_IDS,
	type VaultObservabilityEventDTO,
	WORKBENCH_VIEW_STAGE_MAP,
	type WorkbenchViewId,
} from "@opendv/protocol-ts";
import { WorkbenchPluginRegistry } from "../../plugins/plugin-registry";
import { useWorkbenchStore } from "../../store/workbench-store";
import { VERDICT_VISUAL_GRAMMAR } from "../../theme/theme-registry";

export const SHORTCUT_KEY_TO_VIEW_ID: Readonly<
	Record<string, WorkbenchViewId>
> = {
	"1": "WB-00",
	"2": "WB-01",
	"3": "WB-02",
	"4": "WB-03",
	"5": "WB-04",
	"6": "WB-05",
	"7": "WB-06",
	"8": "WB-07",
	"9": "WB-08",
	"0": "WB-09",
	"-": "WB-10",
	"=": "WB-11",
};

export const VIEW_ID_TO_SHORTCUT_KEY: Readonly<
	Record<WorkbenchViewId, string>
> = {
	"WB-00": "1",
	"WB-01": "2",
	"WB-02": "3",
	"WB-03": "4",
	"WB-04": "5",
	"WB-05": "6",
	"WB-06": "7",
	"WB-07": "8",
	"WB-08": "9",
	"WB-09": "0",
	"WB-10": "-",
	"WB-11": "=",
};

export interface SidebarNavProps {
	activeViewId?: WorkbenchViewId | undefined;
	isCollapsed?: boolean | undefined;
	eventsByView?:
		| Record<WorkbenchViewId, VaultObservabilityEventDTO[]>
		| undefined;
	pluginRegistry?: WorkbenchPluginRegistry | undefined;
	onSelectView?: ((viewId: WorkbenchViewId) => void) | undefined;
	onToggleCollapse?: (() => void) | undefined;
}

export function SidebarNav(props: SidebarNavProps) {
	const storeActiveViewId = useWorkbenchStore((s) => s.activeViewId);
	const storeIsCollapsed = useWorkbenchStore((s) => s.isSidebarCollapsed);
	const storeEventsByView = useWorkbenchStore((s) => s.eventsByView);
	const storeSelectView = useWorkbenchStore((s) => s.setActiveView);
	const storeToggleSidebar = useWorkbenchStore((s) => s.toggleSidebar);

	const activeViewId = props.activeViewId ?? storeActiveViewId;
	const isCollapsed = props.isCollapsed ?? storeIsCollapsed;
	const eventsByView = props.eventsByView ?? storeEventsByView;
	const handleSelectView = props.onSelectView ?? storeSelectView;
	const handleToggleCollapse = props.onToggleCollapse ?? storeToggleSidebar;

	return (
		<aside
			data-testid="sidebar-nav"
			data-collapsed={isCollapsed ? "true" : "false"}
			style={{
				width: isCollapsed ? "56px" : "240px",
				minWidth: isCollapsed ? "56px" : "240px",
				backgroundColor: "var(--dv-surface-1)",
				borderRight: "1px solid var(--dv-border-strong)",
				display: "flex",
				flexDirection: "column",
				transition: "width 150ms ease-in-out",
			}}
		>
			<div
				style={{
					display: "flex",
					alignItems: "center",
					justifyContent: isCollapsed ? "center" : "space-between",
					padding: "0.5rem 0.75rem",
					borderBottom: "1px solid var(--dv-border-subtle)",
				}}
			>
				{!isCollapsed && (
					<span
						style={{
							fontSize: "0.75rem",
							fontWeight: 700,
							color: "var(--dv-text-secondary)",
							textTransform: "uppercase",
							letterSpacing: "0.04em",
						}}
					>
						12-View Workbench
					</span>
				)}
				<button
					type="button"
					aria-expanded={!isCollapsed}
					aria-label={
						isCollapsed
							? "Expand workbench navigation rail"
							: "Collapse workbench navigation rail"
					}
					data-testid="sidebar-collapse-btn"
					onClick={handleToggleCollapse}
					style={{
						backgroundColor: "var(--dv-surface-2)",
						color: "var(--dv-text-primary)",
						border: "1px solid var(--dv-border-strong)",
						borderRadius: "0.25rem",
						padding: "0.15rem 0.4rem",
						fontSize: "0.75rem",
						cursor: "pointer",
					}}
				>
					{isCollapsed ? "»" : "«"}
				</button>
			</div>

			<nav
				aria-label="Workbench Views (WB-00 to WB-11)"
				style={{
					flex: 1,
					overflowY: "auto",
					padding: "0.375rem",
				}}
			>
				<ul
					style={{
						listStyle: "none",
						margin: 0,
						padding: 0,
						display: "flex",
						flexDirection: "column",
						gap: "0.25rem",
					}}
				>
					{ALL_WORKBENCH_VIEW_IDS.map((viewId) => {
						const canonicalMeta = WORKBENCH_VIEW_STAGE_MAP[viewId];
						const registeredPlugin = props.pluginRegistry
							? props.pluginRegistry.getView(viewId)
							: WorkbenchPluginRegistry.getView(viewId);
						const shortTitle =
							registeredPlugin?.shortTitle ?? canonicalMeta.shortTitle;
						const fullTitle = registeredPlugin?.title ?? canonicalMeta.title;
						const sceneNumber =
							registeredPlugin?.sceneNumber ?? canonicalMeta.sceneNumber;
						const viewEvents = eventsByView[viewId] ?? [];
						const latestEvent =
							viewEvents.length > 0
								? viewEvents[viewEvents.length - 1]
								: undefined;
						const verdictMeta = latestEvent
							? VERDICT_VISUAL_GRAMMAR[latestEvent.status]
							: undefined;
						const isActive = viewId === activeViewId;
						const shortcutKey = VIEW_ID_TO_SHORTCUT_KEY[viewId];

						return (
							<li key={viewId}>
								<button
									type="button"
									aria-current={isActive ? "page" : undefined}
									title={`${viewId}: ${fullTitle} (Scene ${sceneNumber}, Shortcut [${shortcutKey}])`}
									data-testid={`nav-view-${viewId}`}
									data-view-id={viewId}
									onClick={() => handleSelectView(viewId)}
									style={{
										width: "100%",
										textAlign: "left",
										display: "flex",
										alignItems: "center",
										justifyContent: isCollapsed ? "center" : "space-between",
										gap: "0.375rem",
										padding: isCollapsed ? "0.4rem 0.25rem" : "0.4rem 0.5rem",
										borderRadius: "0.375rem",
										border: isActive
											? "1.5px solid var(--dv-hub-accent)"
											: "1px solid transparent",
										backgroundColor: isActive
											? "var(--dv-surface-2)"
											: "transparent",
										color: "var(--dv-text-primary)",
										cursor: "pointer",
										fontSize: "0.75rem",
									}}
								>
									<div
										style={{
											display: "flex",
											alignItems: "center",
											gap: "0.375rem",
											minWidth: 0,
										}}
									>
										<code
											style={{
												fontWeight: 700,
												fontSize: "0.7rem",
												color: isActive
													? "var(--dv-hub-accent)"
													: "var(--dv-text-secondary)",
											}}
										>
											{viewId}
										</code>
										{!isCollapsed && (
											<span
												style={{
													overflow: "hidden",
													textOverflow: "ellipsis",
													whiteSpace: "nowrap",
													fontWeight: isActive ? 600 : 400,
												}}
											>
												{shortTitle}
											</span>
										)}
									</div>

									{!isCollapsed && (
										<div
											style={{
												display: "inline-flex",
												alignItems: "center",
												gap: "0.25rem",
												flexShrink: 0,
											}}
										>
											{verdictMeta && (
												<span
													aria-label={`Latest status ${verdictMeta.prefixBadge}`}
													style={{
														color: `var(${verdictMeta.cssColorVar})`,
														fontWeight: 700,
														fontSize: "0.7rem",
													}}
												>
													{verdictMeta.shapeSymbol}
												</span>
											)}
											<kbd
												style={{
													fontSize: "0.65rem",
													color: "var(--dv-text-muted)",
													backgroundColor: "var(--dv-surface-3)",
													borderRadius: "0.2rem",
													padding: "0.05rem 0.25rem",
												}}
											>
												{shortcutKey}
											</kbd>
										</div>
									)}
								</button>
							</li>
						);
					})}
				</ul>
			</nav>
		</aside>
	);
}
