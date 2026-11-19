/**
 * Multi-View Interactive Data Vault 2.0 Schema Explorer (`DvSchemaExplorerView.tsx`).
 * (`FR-DVE-01..07`, `D11`, `D26`, `DESIGN.md`).
 */

import { useMemo } from "react";
import { CLAUDE_TOKENS, EXPLORER_FONTS } from "../design-tokens";
import { safeUseEffect, safeUseState } from "../modals/safeHooks";
import { DvBipartiteGraphTab } from "./DvBipartiteGraphTab";
import { DvContractIrTab } from "./DvContractIrTab";
import { DvTriageAndWaveDiffTab } from "./DvTriageAndWaveDiffTab";
import { DvVaultRowsAndSqlQueryTab } from "./DvVaultRowsAndSqlQueryTab";
import {
	type HydratedRunArtifacts,
	fetchDvRunReportAndArtifacts,
} from "./dv-schema-layout";
import { buildDvEntities, formatTriageCsv } from "./dv-schema-utils";
import type {
	DvSchemaExplorerViewProps,
	DvSchemaProjectionDTO,
	DvSchemaTabId,
} from "./types";

export { formatTriageCsv, type DvSchemaExplorerViewProps, type DvSchemaTabId };

const TAB_DEFS: ReadonlyArray<{ id: DvSchemaTabId; label: string }> = [
	{ id: "graph", label: "Interactive DV 2.0 Graph" },
	{ id: "triage", label: "100% Triage & Wave Diff" },
	{ id: "rows", label: "Vault Rows & Safe SQL" },
	{ id: "sql", label: "5-Zone SQL (Z0–Z4) & IR" },
];

export function DvSchemaExplorerView({
	runId,
	compareRunId,
	sessionId,
	onGoPipeline,
	onGoOntology,
	apiUrl,
	fetchImpl,
	initialTab = "graph",
	initialProjection,
	selectedEntityName: controlledEntityName,
	isDdlDialogOpen: controlledDdlOpen,
	entityDdlSql,
	selectedTableName: controlledTableName,
	tableRowsData,
	sqlQueryText,
	sqlQueryResult,
	viewportWidth: controlledViewportWidth,
	zoneSqlDuckDb,
	zoneSqlDatabricks,
	finalContractYaml: controlledContractYaml,
	finalCanonicalIrJson: controlledIrJson,
}: DvSchemaExplorerViewProps) {
	const [activeTab, setActiveTab] = safeUseState<DvSchemaTabId>(initialTab);
	const [fetchedProjection, setFetchedProjection] =
		safeUseState<DvSchemaProjectionDTO | null>(initialProjection ?? null);
	const [loadError, setLoadError] = safeUseState<string | null>(null);
	const [internalEntityName, setInternalEntityName] = safeUseState<
		string | undefined
	>(controlledEntityName);
	const [internalDdlOpen, setInternalDdlOpen] = safeUseState<boolean>(
		controlledDdlOpen ?? false,
	);
	const [internalTableName, setInternalTableName] = safeUseState<
		string | undefined
	>(controlledTableName);
	const [reportArtifacts, setReportArtifacts] =
		safeUseState<HydratedRunArtifacts>({});
	const [measuredWidth, setMeasuredWidth] = safeUseState<number>(
		typeof window !== "undefined" ? window.innerWidth : 1440,
	);

	safeUseEffect(() => {
		if (
			controlledViewportWidth !== undefined ||
			typeof window === "undefined"
		) {
			return;
		}
		const onResize = () => setMeasuredWidth(window.innerWidth);
		window.addEventListener("resize", onResize);
		return () => window.removeEventListener("resize", onResize);
	}, [controlledViewportWidth]);

	safeUseEffect(() => {
		if (initialProjection !== undefined) {
			setFetchedProjection(initialProjection);
		}
		if (!runId) return;
		const fetchFn =
			fetchImpl ??
			(typeof globalThis.fetch === "function"
				? globalThis.fetch.bind(globalThis)
				: undefined);
		if (!fetchFn) return;
		const base = (apiUrl ?? "")
			.replace(/\/api\/v1\/?$/, "")
			.replace(/\/+$/, "");
		let cancelled = false;
		if (initialProjection === undefined) {
			setLoadError(null);
			const qs = compareRunId
				? `?compare=${encodeURIComponent(compareRunId)}`
				: "";
			void fetchFn(
				`${base}/api/v1/runs/${encodeURIComponent(runId)}/dv-schema${qs}`,
				{ headers: { Accept: "application/json" } },
			)
				.then(async (resp) => {
					if (!resp.ok) {
						throw new Error(`HTTP ${resp.status} loading DV schema`);
					}
					return (await resp.json()) as DvSchemaProjectionDTO;
				})
				.then((proj) => {
					if (!cancelled) setFetchedProjection(proj);
				})
				.catch((err) => {
					if (!cancelled) {
						setLoadError(err instanceof Error ? err.message : String(err));
					}
				});
		}

		void fetchDvRunReportAndArtifacts(base, runId, fetchFn).then((hydrated) => {
			if (!cancelled) setReportArtifacts(hydrated);
		});

		return () => {
			cancelled = true;
		};
	}, [runId, compareRunId, apiUrl, fetchImpl, initialProjection]);

	const projection: DvSchemaProjectionDTO = initialProjection ??
		fetchedProjection ?? {
			run_id: runId ?? "",
			compare_run_id: compareRunId ?? null,
			model_name: "",
			hubs: [],
			links: [],
			satellites: [],
			reference_tables: [],
			pit_tables: [],
			column_triage_ledger: [],
			scorecard: {},
			wave_diff: null,
		};

	const entities = useMemo(() => buildDvEntities(projection), [projection]);
	const availableTables = useMemo(
		() => entities.map((e) => `${e.schemaName}.${e.name}`),
		[entities],
	);

	const viewportWidth = controlledViewportWidth ?? measuredWidth;
	const selectedEntityName =
		controlledEntityName ?? internalEntityName ?? entities[0]?.name;
	const isDdlDialogOpen = controlledDdlOpen ?? internalDdlOpen;
	const selectedTableName =
		controlledTableName ?? internalTableName ?? availableTables[0];

	const hasEntities = entities.length > 0;

	return (
		<section
			data-testid="dv-schema-explorer-view"
			aria-label="DV Schema Explorer"
			style={{
				display: "flex",
				flexDirection: "column",
				minHeight: "calc(100vh - 54px)",
				minWidth: 0,
				maxWidth: "100%",
				background: CLAUDE_TOKENS.colors.canvas,
			}}
		>
			{/* 46px Explorer Sub-Bar */}
			<div
				className="claude-subbar"
				style={{
					background: CLAUDE_TOKENS.colors.surfaceSoft,
					borderBottom: `1px solid ${CLAUDE_TOKENS.colors.hairline}`,
					padding: "8px 24px",
					display: "flex",
					alignItems: "center",
					justifyContent: "space-between",
					flexWrap: "wrap",
					gap: "10px",
				}}
			>
				<div
					style={{
						display: "flex",
						alignItems: "center",
						gap: "8px",
						flexWrap: "wrap",
						minWidth: 0,
						maxWidth: "100%",
					}}
				>
					<h1
						className="claude-h1"
						style={{
							margin: 0,
							fontFamily: EXPLORER_FONTS.serif,
							fontSize: "22px",
							fontWeight: 400,
							color: CLAUDE_TOKENS.colors.ink,
							marginRight: "6px",
						}}
					>
						DV Schema Explorer
					</h1>
					{runId && (
						<span
							style={{
								padding: "3px 9px",
								borderRadius: "9999px",
								background: CLAUDE_TOKENS.colors.surfaceDark,
								color: CLAUDE_TOKENS.colors.onDark,
								fontFamily: EXPLORER_FONTS.mono,
								fontSize: "11px",
							}}
						>
							{projection.model_name ? `${projection.model_name} · ` : ""}
							{runId}
						</span>
					)}
					{compareRunId && (
						<span
							style={{
								padding: "3px 9px",
								borderRadius: "9999px",
								background: CLAUDE_TOKENS.colors.surfaceCreamStrong,
								fontFamily: EXPLORER_FONTS.mono,
								fontSize: "11px",
							}}
						>
							vs {compareRunId}
						</span>
					)}
					{sessionId && (
						<span
							style={{
								padding: "3px 9px",
								borderRadius: "9999px",
								background: CLAUDE_TOKENS.colors.canvas,
								border: `1px solid ${CLAUDE_TOKENS.colors.hairlineStrong}`,
								fontFamily: EXPLORER_FONTS.mono,
								fontSize: "11px",
							}}
						>
							{sessionId}
						</span>
					)}

					<div
						role="tablist"
						aria-label="DV Schema Explorer Views"
						style={{ display: "flex", gap: "6px", flexWrap: "wrap" }}
					>
						{TAB_DEFS.map((t) => {
							const active = activeTab === t.id;
							return (
								<button
									key={t.id}
									type="button"
									role="tab"
									aria-selected={active}
									data-testid={`dv-schema-tab-${t.id}`}
									onClick={() => setActiveTab(t.id)}
									style={{
										padding: "5px 11px",
										borderRadius: "8px",
										fontSize: "11.5px",
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
									{t.label}
								</button>
							);
						})}
					</div>
				</div>

				<div style={{ display: "flex", gap: "8px", flexWrap: "wrap" }}>
					<button
						type="button"
						onClick={onGoOntology}
						className="claude-btn-secondary"
						style={{
							padding: "5px 11px",
							borderRadius: "8px",
							border: `1px solid ${CLAUDE_TOKENS.colors.hairlineStrong}`,
							background: CLAUDE_TOKENS.colors.canvas,
							color: CLAUDE_TOKENS.colors.ink,
							fontSize: "12px",
							cursor: "pointer",
						}}
					>
						Ontology &amp; Pipeline
					</button>
					<button
						type="button"
						onClick={onGoPipeline}
						className="claude-btn-secondary"
						style={{
							padding: "5px 11px",
							borderRadius: "8px",
							border: `1px solid ${CLAUDE_TOKENS.colors.hairlineStrong}`,
							background: CLAUDE_TOKENS.colors.canvas,
							color: CLAUDE_TOKENS.colors.ink,
							fontSize: "12px",
							cursor: "pointer",
						}}
					>
						Run Explorer
					</button>
				</div>
			</div>

			{/* Main Content */}
			<div style={{ padding: "16px 24px", flex: 1, minWidth: 0 }}>
				{loadError && (
					<div
						role="alert"
						style={{
							marginBottom: "12px",
							padding: "10px 14px",
							borderRadius: "10px",
							border: `1px solid ${CLAUDE_TOKENS.colors.error}`,
							background: CLAUDE_TOKENS.colors.surfaceSoft,
							color: CLAUDE_TOKENS.colors.error,
							fontSize: "12.5px",
						}}
					>
						{loadError}
					</div>
				)}

				{!hasEntities && !runId ? (
					<div
						className="claude-card-cream"
						style={{
							padding: "24px",
							background: CLAUDE_TOKENS.colors.surfaceCard,
							border: `1px solid ${CLAUDE_TOKENS.colors.hairline}`,
							borderRadius: "12px",
						}}
					>
						<p style={{ margin: 0, color: CLAUDE_TOKENS.colors.body }}>
							Select a completed pipeline run in Run Explorer or execute the
							6-step Ontology &amp; Pipeline wizard to inspect generated Data
							Vault 2.0 topology, 100% column triage, and persisted DuckDB rows.
						</p>
					</div>
				) : activeTab === "graph" ? (
					<DvBipartiteGraphTab
						projection={projection}
						entities={entities}
						selectedEntityName={selectedEntityName}
						onSelectEntity={(name) => setInternalEntityName(name)}
						isDdlDialogOpen={isDdlDialogOpen}
						onSetDdlDialogOpen={(open) => setInternalDdlOpen(open)}
						entityDdlSql={entityDdlSql}
						onPreviewRows={(tbl) => {
							setInternalTableName(tbl);
							setActiveTab("rows");
						}}
					/>
				) : activeTab === "triage" ? (
					<DvTriageAndWaveDiffTab
						projection={projection}
						runId={runId}
						compareRunId={compareRunId}
					/>
				) : activeTab === "rows" ? (
					<DvVaultRowsAndSqlQueryTab
						runId={runId}
						availableTables={availableTables}
						selectedTableName={selectedTableName}
						tableRowsData={tableRowsData}
						sqlQueryText={sqlQueryText}
						sqlQueryResult={sqlQueryResult}
						viewportWidth={viewportWidth}
						apiUrl={apiUrl}
						fetchImpl={fetchImpl}
						onSelectTable={(t) => setInternalTableName(t)}
					/>
				) : (
					<DvContractIrTab
						projection={projection}
						zoneSqlDuckDb={zoneSqlDuckDb ?? reportArtifacts.zoneSqlDuckDb}
						zoneSqlDatabricks={
							zoneSqlDatabricks ?? reportArtifacts.zoneSqlDatabricks
						}
						finalContractYaml={
							controlledContractYaml ?? reportArtifacts.contractYaml
						}
						finalCanonicalIrJson={controlledIrJson ?? reportArtifacts.irJson}
					/>
				)}
			</div>
		</section>
	);
}
