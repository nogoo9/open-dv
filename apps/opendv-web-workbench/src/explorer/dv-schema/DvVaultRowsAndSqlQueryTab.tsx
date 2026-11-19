/**
 * View 3 (D11): Populated Vault Row Preview & Safe Read-Only SQL Runner
 * (`GET /runs/{id}/dv-schema/tables/{table}/rows`, `POST /runs/{id}/dv-schema/query`).
 * Downgrades SQL runner to view-only below 768px (`D26`).
 */

import { CLAUDE_TOKENS, EXPLORER_FONTS } from "../design-tokens";
import { safeUseEffect, safeUseRef, safeUseState } from "../modals/safeHooks";
import type { DvSchemaQueryResponseDTO, DvSchemaTableRowsDTO } from "./types";

export interface DvVaultRowsAndSqlQueryTabProps {
	runId?: string | null | undefined;
	availableTables: readonly string[];
	selectedTableName?: string | undefined;
	tableRowsData?: DvSchemaTableRowsDTO | null | undefined;
	sqlQueryText?: string | undefined;
	sqlQueryResult?: DvSchemaQueryResponseDTO | null | undefined;
	viewportWidth: number;
	apiUrl?: string | undefined;
	fetchImpl?: typeof fetch | undefined;
	onSelectTable: (table: string) => void;
}

function isZeroOrGhostCell(val: unknown): boolean {
	const s = String(val ?? "");
	return (
		s === "SYSTEM_ZERO_KEY" ||
		s === "SYSTEM_NA_KEY" ||
		/^0{32,}$/.test(s) ||
		/^f{32,}$/i.test(s)
	);
}

export function DvVaultRowsAndSqlQueryTab({
	runId,
	availableTables,
	selectedTableName,
	tableRowsData: controlledRows,
	sqlQueryText: controlledSql,
	sqlQueryResult: controlledSqlResult,
	viewportWidth,
	apiUrl,
	fetchImpl,
	onSelectTable,
}: DvVaultRowsAndSqlQueryTabProps) {
	const activeTable = selectedTableName ?? availableTables[0] ?? "";
	const [fetchedRows, setFetchedRows] =
		safeUseState<DvSchemaTableRowsDTO | null>(controlledRows ?? null);
	const [rowsError, setRowsError] = safeUseState<string | null>(null);
	const [sqlInput, setSqlInput] = safeUseState<string>(
		controlledSql ??
			(activeTable
				? `SELECT * FROM ${activeTable} LIMIT 25;`
				: "SELECT 1 AS ok;"),
	);
	const [queryResult, setQueryResult] =
		safeUseState<DvSchemaQueryResponseDTO | null>(controlledSqlResult ?? null);
	const [queryError, setQueryError] = safeUseState<string | null>(null);
	const [isRunningSql, setIsRunningSql] = safeUseState(false);
	const hasEditedSqlRef = safeUseRef(false);
	const prevTableRef = safeUseRef(activeTable);

	safeUseEffect(() => {
		if (controlledSql !== undefined) {
			setSqlInput(controlledSql);
			return;
		}
		if (prevTableRef.current !== activeTable) {
			prevTableRef.current = activeTable;
			if (!hasEditedSqlRef.current) {
				setSqlInput(
					activeTable
						? `SELECT * FROM ${activeTable} LIMIT 25;`
						: "SELECT 1 AS ok;",
				);
			}
		}
	}, [activeTable, controlledSql]);

	const isMobileViewOnly = viewportWidth < 768;
	const base = (apiUrl ?? "").replace(/\/api\/v1\/?$/, "").replace(/\/+$/, "");

	safeUseEffect(() => {
		if (controlledRows !== undefined) {
			setFetchedRows(controlledRows);
			return;
		}
		if (!runId || !activeTable) return;
		const fetchFn =
			fetchImpl ??
			(typeof globalThis.fetch === "function"
				? globalThis.fetch.bind(globalThis)
				: undefined);
		if (!fetchFn) return;
		let cancelled = false;
		setRowsError(null);
		void fetchFn(
			`${base}/api/v1/runs/${encodeURIComponent(runId)}/dv-schema/tables/${encodeURIComponent(activeTable)}/rows?limit=50`,
			{ headers: { Accept: "application/json" } },
		)
			.then(async (resp) => {
				if (!resp.ok) {
					const errText = await resp.text();
					throw new Error(errText || `HTTP ${resp.status}`);
				}
				return (await resp.json()) as DvSchemaTableRowsDTO;
			})
			.then((data) => {
				if (!cancelled) setFetchedRows(data);
			})
			.catch((err) => {
				if (!cancelled) {
					setRowsError(err instanceof Error ? err.message : String(err));
				}
			});
		return () => {
			cancelled = true;
		};
	}, [runId, activeTable, base, fetchImpl, controlledRows]);

	const activeRows = controlledRows ?? fetchedRows;
	const activeQueryResult = controlledSqlResult ?? queryResult;

	const handleRunSql = async () => {
		if (isMobileViewOnly || !runId || isRunningSql) return;
		const fetchFn =
			fetchImpl ??
			(typeof globalThis.fetch === "function"
				? globalThis.fetch.bind(globalThis)
				: undefined);
		if (!fetchFn) return;
		setIsRunningSql(true);
		setQueryError(null);
		try {
			const resp = await fetchFn(
				`${base}/api/v1/runs/${encodeURIComponent(runId)}/dv-schema/query`,
				{
					method: "POST",
					headers: {
						"Content-Type": "application/json",
						Accept: "application/json",
					},
					body: JSON.stringify({ sql: sqlInput, max_rows: 500 }),
				},
			);
			const body = (await resp.json()) as DvSchemaQueryResponseDTO & {
				detail?: string;
			};
			if (!resp.ok) {
				throw new Error(body.detail || `Query rejected (HTTP ${resp.status})`);
			}
			setQueryResult(body);
		} catch (err) {
			setQueryError(err instanceof Error ? err.message : String(err));
		} finally {
			setIsRunningSql(false);
		}
	};

	return (
		<div
			style={{
				display: "flex",
				flexDirection: "column",
				gap: "14px",
				minWidth: 0,
			}}
		>
			{/* Populated Vault Row Preview */}
			<div
				style={{
					background: CLAUDE_TOKENS.colors.canvas,
					border: `1px solid ${CLAUDE_TOKENS.colors.hairline}`,
					borderRadius: "12px",
					padding: "12px 14px",
					display: "flex",
					flexDirection: "column",
					gap: "10px",
					minWidth: 0,
					boxSizing: "border-box",
				}}
			>
				<div
					style={{
						display: "flex",
						justifyContent: "space-between",
						alignItems: "center",
						flexWrap: "wrap",
						gap: "8px",
					}}
				>
					<div>
						<div
							style={{
								fontSize: "10px",
								textTransform: "uppercase",
								fontWeight: 600,
								letterSpacing: "0.6px",
								color: CLAUDE_TOKENS.colors.primaryActive,
							}}
						>
							Persisted DuckDB Sandbox · Zero Keys &amp; Ghost Record Preview
						</div>
						<h2
							className="claude-h2"
							style={{ margin: 0, fontFamily: EXPLORER_FONTS.serif }}
						>
							Populated Vault Rows ({activeRows?.table_name ?? activeTable})
						</h2>
					</div>

					<select
						aria-label="Select Vault table to preview rows"
						value={activeTable}
						onChange={(ev) => onSelectTable(ev.target.value)}
						style={{
							padding: "5px 10px",
							borderRadius: "8px",
							border: `1px solid ${CLAUDE_TOKENS.colors.hairlineStrong}`,
							background: CLAUDE_TOKENS.colors.canvas,
							fontFamily: EXPLORER_FONTS.mono,
							fontSize: "11.5px",
						}}
					>
						{availableTables.map((t) => (
							<option key={t} value={t}>
								{t}
							</option>
						))}
					</select>
				</div>

				{rowsError && (
					<div
						role="alert"
						style={{
							padding: "8px 10px",
							borderRadius: "8px",
							border: `1px solid ${CLAUDE_TOKENS.colors.error}`,
							color: CLAUDE_TOKENS.colors.error,
							fontSize: "11.5px",
						}}
					>
						{rowsError}
					</div>
				)}

				<div style={{ overflowX: "auto" }}>
					<table
						style={{
							width: "100%",
							borderCollapse: "collapse",
							fontSize: "11px",
							fontFamily: EXPLORER_FONTS.mono,
						}}
					>
						<thead>
							<tr>
								{(activeRows?.columns ?? []).map((col) => (
									<th
										key={col}
										style={{
											textAlign: "left",
											padding: "6px 8px",
											background: CLAUDE_TOKENS.colors.surfaceSoft,
											color: CLAUDE_TOKENS.colors.muted,
											fontSize: "10px",
											textTransform: "uppercase",
										}}
									>
										{col}
									</th>
								))}
							</tr>
						</thead>
						<tbody>
							{(activeRows?.rows ?? []).length === 0 ? (
								<tr>
									<td
										colSpan={Math.max(1, activeRows?.columns?.length ?? 1)}
										style={{
											padding: "14px",
											textAlign: "center",
											color: CLAUDE_TOKENS.colors.muted,
										}}
									>
										No persisted DuckDB rows loaded for this table yet.
									</td>
								</tr>
							) : (
								(activeRows?.rows ?? []).map((row, idx) => {
									const isSystemRow =
										Object.values(row).some(isZeroOrGhostCell);
									const rowKey = `${String(Object.values(row)[0] ?? "row")}-${idx}`;
									return (
										<tr
											key={rowKey}
											style={{
												background: isSystemRow
													? CLAUDE_TOKENS.colors.surfaceSoft
													: "transparent",
												borderBottom: `1px solid ${CLAUDE_TOKENS.colors.hairlineSoft}`,
											}}
										>
											{(activeRows?.columns ?? []).map((col) => (
												<td
													key={col}
													style={{
														padding: "6px 8px",
														color: isZeroOrGhostCell(row[col])
															? CLAUDE_TOKENS.colors.primaryActive
															: CLAUDE_TOKENS.colors.ink,
													}}
												>
													{String(row[col] ?? "")}
												</td>
											))}
										</tr>
									);
								})
							)}
						</tbody>
					</table>
				</div>
			</div>

			{/* Safe Read-Only SQL Runner (D11) */}
			<div
				className="claude-card-dark"
				style={{
					background: CLAUDE_TOKENS.colors.surfaceDark,
					color: CLAUDE_TOKENS.colors.onDark,
					borderRadius: "12px",
					padding: "14px",
					display: "flex",
					flexDirection: "column",
					gap: "10px",
				}}
			>
				<div
					style={{
						display: "flex",
						justifyContent: "space-between",
						alignItems: "center",
						flexWrap: "wrap",
						gap: "8px",
					}}
				>
					<div>
						<div
							style={{
								fontSize: "10px",
								textTransform: "uppercase",
								fontWeight: 600,
								letterSpacing: "0.6px",
								color: CLAUDE_TOKENS.colors.accentAmber,
							}}
						>
							Safe Read-Only SQL Runner (D11 · SELECT / UNION Only)
						</div>
						<div style={{ fontSize: "13px", fontWeight: 500 }}>
							Execute Ad-Hoc Read-Only DuckDB &amp; PIT D.10b Queries
						</div>
					</div>

					{isMobileViewOnly && (
						<span
							data-testid="mobile-sql-view-only-badge"
							style={{
								padding: "3px 9px",
								borderRadius: "9999px",
								background: CLAUDE_TOKENS.colors.surfaceDarkElevated,
								border: `1px solid ${CLAUDE_TOKENS.colors.warning}`,
								color: CLAUDE_TOKENS.colors.warning,
								fontSize: "11px",
							}}
						>
							View-only (&lt;768px mobile viewport)
						</span>
					)}
				</div>

				<textarea
					data-testid="dv-sql-runner-textarea"
					aria-label="Safe read-only SQL query input"
					rows={4}
					readOnly={isMobileViewOnly}
					value={sqlInput}
					onChange={(ev) => {
						hasEditedSqlRef.current = true;
						setSqlInput(ev.target.value);
					}}
					style={{
						width: "100%",
						boxSizing: "border-box",
						padding: "10px",
						borderRadius: "8px",
						border: `1px solid ${CLAUDE_TOKENS.colors.body}`,
						background: CLAUDE_TOKENS.colors.surfaceDarkSoft,
						color: CLAUDE_TOKENS.colors.onDark,
						fontFamily: EXPLORER_FONTS.mono,
						fontSize: "11.5px",
					}}
				/>

				<div
					style={{
						display: "flex",
						justifyContent: "space-between",
						alignItems: "center",
						gap: "8px",
						flexWrap: "wrap",
					}}
				>
					<span
						style={{
							fontSize: "11px",
							color: CLAUDE_TOKENS.colors.onDarkSoft,
						}}
					>
						Guarded by validate_safe_sql() + enable_external_access=False
					</span>
					<button
						type="button"
						data-testid="btn-run-safe-sql"
						disabled={isMobileViewOnly || isRunningSql}
						onClick={() => void handleRunSql()}
						style={{
							padding: "7px 14px",
							borderRadius: "8px",
							border: "none",
							background: isMobileViewOnly
								? CLAUDE_TOKENS.colors.body
								: CLAUDE_TOKENS.colors.primary,
							color: CLAUDE_TOKENS.colors.onPrimary,
							fontSize: "12px",
							fontWeight: 500,
							cursor: isMobileViewOnly ? "not-allowed" : "pointer",
						}}
					>
						{isRunningSql ? "Running..." : "Run Read-Only SQL"}
					</button>
				</div>

				{queryError && (
					<div
						role="alert"
						style={{
							padding: "8px 10px",
							borderRadius: "8px",
							background: CLAUDE_TOKENS.colors.surfaceDarkElevated,
							border: `1px solid ${CLAUDE_TOKENS.colors.error}`,
							color: CLAUDE_TOKENS.colors.onDark,
							fontSize: "11.5px",
						}}
					>
						{queryError}
					</div>
				)}

				{activeQueryResult && (
					<div
						style={{
							background: CLAUDE_TOKENS.colors.surfaceDarkSoft,
							border: "1px solid #33312d",
							borderRadius: "8px",
							padding: "10px",
							overflowX: "auto",
						}}
					>
						<div
							style={{
								fontSize: "10.5px",
								color: CLAUDE_TOKENS.colors.accentTeal,
								marginBottom: "6px",
								fontFamily: EXPLORER_FONTS.mono,
							}}
						>
							{activeQueryResult.row_count} rows ·{" "}
							{activeQueryResult.duration_ms.toFixed(1)} ms
						</div>
						<table
							style={{
								width: "100%",
								borderCollapse: "collapse",
								fontSize: "11px",
								fontFamily: EXPLORER_FONTS.mono,
							}}
						>
							<thead>
								<tr>
									{(activeQueryResult.columns ?? []).map((c) => (
										<th
											key={c}
											style={{
												textAlign: "left",
												padding: "5px 8px",
												color: CLAUDE_TOKENS.colors.onDarkSoft,
												borderBottom: "1px solid #33312d",
											}}
										>
											{c}
										</th>
									))}
								</tr>
							</thead>
							<tbody>
								{(activeQueryResult.rows ?? []).map((r, i) => {
									const qRowKey = `${String(Object.values(r)[0] ?? "qrow")}-${i}`;
									return (
										<tr key={qRowKey}>
											{(activeQueryResult.columns ?? []).map((c) => (
												<td
													key={c}
													style={{
														padding: "5px 8px",
														color: CLAUDE_TOKENS.colors.onDark,
														borderBottom: "1px solid #252320",
													}}
												>
													{String(r[c] ?? "")}
												</td>
											))}
										</tr>
									);
								})}
							</tbody>
						</table>
					</div>
				)}
			</div>
		</div>
	);
}
