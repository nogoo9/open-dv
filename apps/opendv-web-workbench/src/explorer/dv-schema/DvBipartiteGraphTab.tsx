/**
 * View 1 (06b): Interactive Bipartite Hub-Link-Sat-PIT Graph (`elkjs` + `@xyflow/react`)
 * with 9 Zone/PII/Wave-Delta Filter Pills, Live Search, 455px Inspector & Native `<dialog>` DDL.
 */

import {
	Background,
	Controls,
	Handle,
	MiniMap,
	type Node,
	type NodeProps,
	Position,
	ReactFlow,
} from "@xyflow/react";
import { useMemo } from "react";
import { CLAUDE_TOKENS, EXPLORER_FONTS } from "../design-tokens";
import { safeUseEffect, safeUseRef, safeUseState } from "../modals/safeHooks";
import { DvTableLineageInspector } from "./DvTableLineageInspector";
import {
	type DvBipartiteNodeData,
	buildBipartiteFlowElements,
	generateEntityDdlDml,
	layoutDvEntitiesWithElk,
} from "./dv-schema-layout";
import { filterDvEntities } from "./dv-schema-utils";
import type {
	DvGraphEntity,
	DvSchemaProjectionDTO,
	DvSchemaZoneFilter,
} from "./types";

const FILTER_PILLS: ReadonlyArray<{ id: DvSchemaZoneFilter; label: string }> = [
	{ id: "all", label: "All Zones" },
	{ id: "z0_refs", label: "Z0 Refs" },
	{ id: "z1_staging", label: "Z1 Staging" },
	{ id: "z2_hubs", label: "Z2 Hubs" },
	{ id: "z2_links", label: "Z2 Links & SAL" },
	{ id: "z2_sats", label: "Z2 Satellites" },
	{ id: "z3_pit", label: "Z3 PIT & Bridges" },
	{ id: "pii_only", label: "PII Split Only" },
	{ id: "wave_delta", label: "+ Wave Delta" },
];

function DvEntityNodeCard({ data }: NodeProps<Node<DvBipartiteNodeData>>) {
	const { entity, isSelected } = data;
	const isHub = entity.kind === "HUB";
	const isLink = entity.kind === "LINK" || entity.kind === "SAL";
	const isPit = entity.kind === "PIT";

	return (
		<div
			data-testid={`dv-node-${entity.name}`}
			style={{
				minWidth: "200px",
				maxWidth: "240px",
				padding: "8px 12px",
				borderRadius: isLink ? "20px" : "8px",
				background: isHub
					? CLAUDE_TOKENS.colors.surfaceDark
					: isSelected
						? CLAUDE_TOKENS.colors.surfaceCreamStrong
						: isLink
							? CLAUDE_TOKENS.colors.surfaceCard
							: CLAUDE_TOKENS.colors.canvas,
				color: isHub ? CLAUDE_TOKENS.colors.onDark : CLAUDE_TOKENS.colors.ink,
				border: isSelected
					? `2px solid ${CLAUDE_TOKENS.colors.primary}`
					: isPit
						? `1.5px solid ${CLAUDE_TOKENS.colors.accentTeal}`
						: entity.isPii
							? `1.5px dashed ${CLAUDE_TOKENS.colors.primary}`
							: `1.5px solid ${
									isHub
										? CLAUDE_TOKENS.colors.body
										: CLAUDE_TOKENS.colors.hairlineStrong
								}`,
				fontFamily: EXPLORER_FONTS.mono,
				fontSize: "10.5px",
			}}
		>
			<Handle type="target" position={Position.Left} />
			<div
				style={{
					display: "flex",
					justifyContent: "space-between",
					alignItems: "center",
					gap: "6px",
				}}
			>
				<strong
					style={{
						fontSize: "11px",
						overflow: "hidden",
						textOverflow: "ellipsis",
					}}
				>
					{entity.name}
				</strong>
				<span style={{ fontSize: "9px", opacity: 0.85 }}>{entity.kind}</span>
			</div>
			<div
				style={{
					marginTop: "3px",
					fontSize: "9.5px",
					color: isHub
						? CLAUDE_TOKENS.colors.onDarkSoft
						: CLAUDE_TOKENS.colors.muted,
					overflow: "hidden",
					textOverflow: "ellipsis",
					whiteSpace: "nowrap",
				}}
			>
				{entity.subtitle}
			</div>
			<Handle type="source" position={Position.Right} />
		</div>
	);
}

const NODE_TYPES = { dvEntityNode: DvEntityNodeCard };

export interface DvBipartiteGraphTabProps {
	projection: DvSchemaProjectionDTO;
	entities: readonly DvGraphEntity[];
	selectedEntityName?: string | undefined;
	onSelectEntity: (name: string) => void;
	isDdlDialogOpen: boolean;
	onSetDdlDialogOpen: (open: boolean) => void;
	entityDdlSql?: { duckdb: string; databricks: string } | undefined;
	onPreviewRows: (tableName: string) => void;
}

export function DvBipartiteGraphTab({
	projection,
	entities,
	selectedEntityName,
	onSelectEntity,
	isDdlDialogOpen,
	onSetDdlDialogOpen,
	entityDdlSql,
	onPreviewRows,
}: DvBipartiteGraphTabProps) {
	const [zoneFilter, setZoneFilter] = safeUseState<DvSchemaZoneFilter>("all");
	const [searchQuery, setSearchQuery] = safeUseState("");
	const [ddlDialect, setDdlDialect] = safeUseState<"duckdb" | "databricks">(
		"duckdb",
	);
	const ddlDialogRef = safeUseRef<HTMLDialogElement | null>(null);

	safeUseEffect(() => {
		const dlg = ddlDialogRef.current;
		if (!dlg) return;
		if (isDdlDialogOpen) {
			if (!dlg.open && typeof dlg.showModal === "function") {
				dlg.showModal();
			}
		} else if (dlg.open && typeof dlg.close === "function") {
			dlg.close();
		}
	}, [isDdlDialogOpen]);

	const filteredEntities = useMemo(
		() => filterDvEntities(entities, zoneFilter, searchQuery),
		[entities, zoneFilter, searchQuery],
	);

	const selectedEntity = useMemo(
		() =>
			entities.find((e) => e.name === selectedEntityName) ??
			filteredEntities[0] ??
			entities[0] ??
			null,
		[entities, filteredEntities, selectedEntityName],
	);

	const baseElements = useMemo(
		() => buildBipartiteFlowElements(filteredEntities, selectedEntity?.name),
		[filteredEntities, selectedEntity?.name],
	);

	const [layoutedNodes, setLayoutedNodes] = safeUseState<
		Node<DvBipartiteNodeData>[]
	>(baseElements.nodes);

	safeUseEffect(() => {
		let cancelled = false;
		setLayoutedNodes(baseElements.nodes);
		void layoutDvEntitiesWithElk(baseElements.nodes, baseElements.edges).then(
			(next) => {
				if (!cancelled) setLayoutedNodes(next);
			},
		);
		return () => {
			cancelled = true;
		};
	}, [baseElements]);

	const activeDdl =
		entityDdlSql ??
		(selectedEntity
			? generateEntityDdlDml(selectedEntity)
			: { duckdb: "-- Select an entity", databricks: "-- Select an entity" });

	return (
		<div
			style={{
				display: "flex",
				flexDirection: "column",
				gap: "12px",
				minWidth: 0,
			}}
		>
			{/* Zone / PII / Wave-Delta Filter Strip */}
			<div
				className="claude-card-cream"
				style={{
					background: CLAUDE_TOKENS.colors.surfaceCard,
					border: `1px solid ${CLAUDE_TOKENS.colors.hairline}`,
					borderRadius: "12px",
					padding: "8px 12px",
					display: "flex",
					justifyContent: "space-between",
					alignItems: "center",
					gap: "10px",
					flexWrap: "wrap",
				}}
			>
				<div
					style={{
						display: "flex",
						alignItems: "center",
						gap: "6px",
						flexWrap: "wrap",
					}}
				>
					<strong style={{ fontSize: "12px", color: CLAUDE_TOKENS.colors.ink }}>
						Filter Schema:
					</strong>
					{FILTER_PILLS.map((pill) => {
						const active = zoneFilter === pill.id;
						return (
							<button
								key={pill.id}
								type="button"
								data-testid={`filter-pill-${pill.id}`}
								onClick={() => setZoneFilter(pill.id)}
								style={{
									padding: "3px 10px",
									borderRadius: "9999px",
									fontSize: "11px",
									fontWeight: 500,
									cursor: "pointer",
									border: `1px solid ${
										active
											? CLAUDE_TOKENS.colors.primary
											: CLAUDE_TOKENS.colors.hairlineStrong
									}`,
									background: active
										? CLAUDE_TOKENS.colors.primary
										: CLAUDE_TOKENS.colors.canvas,
									color: active
										? CLAUDE_TOKENS.colors.onPrimary
										: CLAUDE_TOKENS.colors.ink,
								}}
							>
								{pill.label}
							</button>
						);
					})}
				</div>

				<input
					type="search"
					data-testid="dv-schema-search-input"
					placeholder="Search hub_*, lnk_*, sat_*, column..."
					value={searchQuery}
					onChange={(ev) => setSearchQuery(ev.target.value)}
					style={{
						width: "min(240px, 100%)",
						maxWidth: "100%",
						padding: "5px 10px",
						borderRadius: "8px",
						border: `1px solid ${CLAUDE_TOKENS.colors.hairlineStrong}`,
						background: CLAUDE_TOKENS.colors.canvas,
						color: CLAUDE_TOKENS.colors.ink,
						fontSize: "12px",
					}}
				/>
			</div>

			{/* Bipartite Topology + 455px Inspector */}
			<div
				className="claude-responsive-grid"
				style={{
					display: "grid",
					gridTemplateColumns: "minmax(0, 1fr) 455px",
					gap: "14px",
					alignItems: "stretch",
				}}
			>
				<div
					className="dag-dot-grid canvas-flow-wrapper"
					style={{
						position: "relative",
						minHeight: "540px",
						minWidth: 0,
						borderRadius: "12px",
						border: `1px solid ${CLAUDE_TOKENS.colors.hairline}`,
						overflow: "hidden",
						display: "flex",
						flexDirection: "column",
					}}
				>
					<div
						style={{
							display: "flex",
							justifyContent: "space-between",
							alignItems: "center",
							padding: "10px 12px",
							gap: "8px",
							flexWrap: "wrap",
							zIndex: 2,
						}}
					>
						<div style={{ display: "flex", gap: "6px", flexWrap: "wrap" }}>
							<span
								style={{
									padding: "3px 9px",
									borderRadius: "9999px",
									background: CLAUDE_TOKENS.colors.canvas,
									border: `1px solid ${CLAUDE_TOKENS.colors.hairline}`,
									fontSize: "11px",
									fontWeight: 500,
								}}
							>
								Bipartite Hub-Link-Satellite-PIT View
							</span>
							<span
								style={{
									padding: "3px 9px",
									borderRadius: "9999px",
									background: CLAUDE_TOKENS.colors.canvas,
									border: `1px solid ${CLAUDE_TOKENS.colors.hairline}`,
									fontSize: "11px",
									fontWeight: 500,
								}}
							>
								Zero Link-on-Link (INV-05 ✓)
							</span>
						</div>
						<div style={{ display: "flex", gap: "5px", flexWrap: "wrap" }}>
							{filteredEntities.map((e) => (
								<button
									key={e.id}
									type="button"
									onClick={() => onSelectEntity(e.name)}
									style={{
										padding: "2px 8px",
										borderRadius: "9999px",
										fontSize: "10px",
										fontFamily: EXPLORER_FONTS.mono,
										cursor: "pointer",
										border: `1px solid ${
											selectedEntity?.name === e.name
												? CLAUDE_TOKENS.colors.primary
												: CLAUDE_TOKENS.colors.hairlineStrong
										}`,
										background:
											selectedEntity?.name === e.name
												? CLAUDE_TOKENS.colors.surfaceCreamStrong
												: CLAUDE_TOKENS.colors.canvas,
										color: CLAUDE_TOKENS.colors.ink,
									}}
								>
									{e.name}
								</button>
							))}
						</div>
					</div>

					<div style={{ flex: 1, minHeight: "460px" }}>
						<ReactFlow
							nodes={layoutedNodes}
							edges={baseElements.edges}
							nodeTypes={NODE_TYPES}
							onNodeClick={(_, node) => {
								const entityName = (node.data as DvBipartiteNodeData)?.entity
									?.name;
								if (entityName) onSelectEntity(entityName);
							}}
							fitView
							deleteKeyCode={null}
						>
							<Background color="#d5cdc2" gap={18} size={1} />
							<Controls showInteractive={false} />
							<MiniMap
								nodeColor="#e8e0d2"
								maskColor="rgba(250, 249, 245, 0.72)"
							/>
						</ReactFlow>
					</div>
				</div>

				<DvTableLineageInspector
					entity={selectedEntity}
					scorecard={projection.scorecard}
					onOpenDdlDialog={() => onSetDdlDialogOpen(true)}
					onPreviewRows={onPreviewRows}
				/>
			</div>

			{/* Native <dialog> for Table DDL / DML (overscroll-behavior: contain via dialog:modal) */}
			{isDdlDialogOpen && (
				<dialog
					ref={ddlDialogRef}
					open={typeof HTMLDialogElement === "undefined" ? true : undefined}
					onCancel={(ev) => {
						ev.preventDefault();
						onSetDdlDialogOpen(false);
					}}
					aria-modal="true"
					aria-label="Entity DDL and DML Inspector"
					data-testid="dv-entity-ddl-dialog"
					style={{
						position: "fixed",
						inset: 0,
						margin: "auto",
						width: "min(780px, 92vw)",
						maxHeight: "82vh",
						padding: "18px",
						borderRadius: "12px",
						border: `1px solid ${CLAUDE_TOKENS.colors.hairlineStrong}`,
						background: CLAUDE_TOKENS.colors.canvas,
						color: CLAUDE_TOKENS.colors.ink,
						boxShadow: "0 18px 48px rgba(20, 20, 19, 0.28)",
						zIndex: 1100,
					}}
				>
					<div
						style={{
							display: "flex",
							justifyContent: "space-between",
							alignItems: "center",
							marginBottom: "10px",
							gap: "8px",
						}}
					>
						<h3
							className="claude-h2"
							style={{ margin: 0, fontFamily: EXPLORER_FONTS.serif }}
						>
							{selectedEntity
								? `${selectedEntity.schemaName}.${selectedEntity.name} DDL / DML`
								: "Table DDL / DML"}
						</h3>
						<div style={{ display: "flex", gap: "6px" }}>
							<button
								type="button"
								onClick={() => setDdlDialect("duckdb")}
								style={{
									padding: "4px 10px",
									borderRadius: "6px",
									fontSize: "11.5px",
									cursor: "pointer",
									border: `1px solid ${CLAUDE_TOKENS.colors.hairlineStrong}`,
									background:
										ddlDialect === "duckdb"
											? CLAUDE_TOKENS.colors.primary
											: CLAUDE_TOKENS.colors.canvas,
									color:
										ddlDialect === "duckdb"
											? CLAUDE_TOKENS.colors.onPrimary
											: CLAUDE_TOKENS.colors.ink,
								}}
							>
								DuckDB
							</button>
							<button
								type="button"
								onClick={() => setDdlDialect("databricks")}
								style={{
									padding: "4px 10px",
									borderRadius: "6px",
									fontSize: "11.5px",
									cursor: "pointer",
									border: `1px solid ${CLAUDE_TOKENS.colors.hairlineStrong}`,
									background:
										ddlDialect === "databricks"
											? CLAUDE_TOKENS.colors.primary
											: CLAUDE_TOKENS.colors.canvas,
									color:
										ddlDialect === "databricks"
											? CLAUDE_TOKENS.colors.onPrimary
											: CLAUDE_TOKENS.colors.ink,
								}}
							>
								Databricks Delta
							</button>
							<button
								type="button"
								onClick={() => onSetDdlDialogOpen(false)}
								style={{
									padding: "4px 10px",
									borderRadius: "6px",
									fontSize: "11.5px",
									cursor: "pointer",
									border: `1px solid ${CLAUDE_TOKENS.colors.hairlineStrong}`,
									background: CLAUDE_TOKENS.colors.canvas,
									color: CLAUDE_TOKENS.colors.ink,
								}}
							>
								Close
							</button>
						</div>
					</div>
					<pre
						style={{
							margin: 0,
							padding: "12px",
							borderRadius: "8px",
							background: CLAUDE_TOKENS.colors.surfaceDark,
							color: CLAUDE_TOKENS.colors.onDark,
							fontFamily: EXPLORER_FONTS.mono,
							fontSize: "11.5px",
							overflow: "auto",
							maxHeight: "56vh",
						}}
					>
						{ddlDialect === "duckdb" ? activeDdl.duckdb : activeDdl.databricks}
					</pre>
				</dialog>
			)}
		</div>
	);
}
