import type { SourceFeedDTO } from "@opendv/protocol-ts";
import { useEffect, useMemo, useState } from "react";
import { getStudioAuthHeaders } from "../../ontology/store/studio-store";
import { EXPLORER_COLORS as C, EXPLORER_FONTS as F } from "../design-tokens";

export interface InspectedColumn {
	name: string;
	data_type: string;
	physical_type?: string;
	is_primary_key?: boolean;
	primary_key_ordinal?: number;
	foreign_key_ref?: string | null;
	governance_tags?: Record<string, string | boolean>;
}

export interface InspectedTable {
	table_name: string;
	estimated_rows?: number | undefined;
	row_count_estimate?: number | undefined;
	columns?: InspectedColumn[] | undefined;
}

export interface CatalogTablePickerModalProps {
	isOpen: boolean;
	onClose: () => void;
	sourceType: "sqlite" | "databricks" | "duckdb" | "file" | string;
	apiUrl?: string | undefined;
	fetchImpl?: typeof fetch | undefined;
	initialUri?: string | undefined;
	initialCatalog?: string;
	initialSchema?: string;
	initialCredentialsRef?: string;
	initialTables?: InspectedTable[];
	initialFingerprint?: string;
	onApply: (feed: SourceFeedDTO) => void;
}

const S = {
	row: { display: "flex", alignItems: "center" } as const,
	between: { display: "flex", alignItems: "center", justifyContent: "space-between" } as const,
	input: { padding: "4px 7px", borderRadius: 4, border: `1px solid ${C.borderLine}`, fontSize: 12 } as const,
	btn: { border: `1px solid ${C.borderLine}`, borderRadius: 4, backgroundColor: C.bgSurface, padding: "2px 6px", fontSize: 11, cursor: "pointer" } as const,
	pk: { backgroundColor: "#EDE9FE", color: "#6D28D9", padding: "1px 4px", borderRadius: 3, fontWeight: 700, marginRight: 4 } as const,
	fk: { backgroundColor: "#E0F2FE", color: "#0369A1", padding: "1px 4px", borderRadius: 3, fontWeight: 600 } as const,
	tag: { backgroundColor: "#FEF3C7", color: "#92400E", padding: "1px 4px", borderRadius: 3, fontWeight: 600, marginRight: 4 } as const,
	panel: { width: "100%", maxWidth: 760, maxHeight: "88vh", backgroundColor: C.bgSurface, borderRadius: 8, border: `1px solid ${C.borderLine}`, display: "flex", flexDirection: "column" as const, overflow: "hidden" as const },
	header: { padding: "12px 16px", borderBottom: `1px solid ${C.borderLine}`, display: "flex", alignItems: "center", justifyContent: "space-between" } as const,
	subbar: { padding: "8px 16px", backgroundColor: C.bgPage, borderBottom: `1px solid ${C.borderLine}`, display: "flex", flexDirection: "column" as const, gap: 6 },
	body: { flex: 1, overflowY: "auto" as const, padding: "12px 16px", display: "flex", flexDirection: "column" as const, gap: 8 },
	footer: { padding: "10px 16px", borderTop: `1px solid ${C.borderLine}`, backgroundColor: C.bgPage, display: "flex", alignItems: "center", justifyContent: "space-between" } as const,
	tblHdr: { display: "flex", alignItems: "center", gap: 8, padding: "6px 10px", backgroundColor: "#F8FAFC", borderRadius: 4, border: "1px solid #E2E8F0", fontSize: 12, fontWeight: 600 } as const,
	overlay: { position: "fixed" as const, inset: 0, zIndex: 9999, backgroundColor: "rgba(15, 23, 42, 0.65)", display: "flex", alignItems: "center", justifyContent: "center", padding: 16 },
	tbl: { width: "100%", fontSize: 11, borderCollapse: "collapse" as const },
	td: { padding: 3 },
	tdName: { padding: 3, fontFamily: F.mono, fontWeight: 600 },
	tdType: { padding: 3, fontFamily: F.mono, color: "#475569" },
	closeBtn: { background: "none", border: "none", fontSize: 16, cursor: "pointer", color: C.inkMuted },
	rowEst: { fontSize: 11, color: C.inkMuted, backgroundColor: "#F1F5F9", padding: "1px 4px", borderRadius: 3 },
	colPanel: { borderTop: `1px solid ${C.borderLine}`, padding: "6px 10px", backgroundColor: "#FFF" },
	loading: { textAlign: "center" as const, padding: 16, color: C.inkMuted, fontSize: 12 },
};

const badgeStyle = (isSql: boolean) => ({ padding: "2px 6px", borderRadius: 4, fontSize: 11, fontFamily: F.mono, fontWeight: 700, backgroundColor: isSql ? "#E0E7FF" : "#FEF3C7", color: isSql ? "#3730A3" : "#92400E" });
const applyBtnStyle = (d: boolean) => ({ padding: "5px 14px", borderRadius: 4, border: "none", backgroundColor: d ? "#94A3B8" : C.borderSelected, color: "#FFF", fontWeight: 600, fontSize: 12, cursor: d ? "not-allowed" : "pointer" });
const rowWrapStyle = (chk: boolean) => ({ borderRadius: 4, border: `1px solid ${chk ? "#93C5FD" : C.borderLine}`, backgroundColor: chk ? "#F0F9FF" : C.bgSurface });

const renderKeyBadges = (c: InspectedColumn) => {
	const fkStr = typeof c.foreign_key_ref === "object" && c.foreign_key_ref !== null ? `${(c.foreign_key_ref as any).target_table || ""}.${(c.foreign_key_ref as any).target_column || ""}` : String(c.foreign_key_ref ?? "");
	return (
		<td style={S.td}>
			{c.is_primary_key && <span data-testid={`pk-badge-${c.name}`} style={S.pk}>PK{c.primary_key_ordinal ? ` (#${c.primary_key_ordinal})` : ""}</span>}
			{c.foreign_key_ref && <span data-testid={`fk-badge-${c.name}`} style={S.fk}>FK ➔ {fkStr}</span>}
		</td>
	);
};

const renderTagBadges = (c: InspectedColumn) => (
	<td style={S.td}>
		{c.governance_tags && Object.entries(c.governance_tags).map(([k, v]) => (
			<span key={k} data-testid={`tag-${k}`} style={S.tag}>{k}: {typeof v === "object" ? JSON.stringify(v) : String(v)}</span>
		))}
	</td>
);

export function CatalogTablePickerModal({
	isOpen,
	onClose,
	sourceType,
	apiUrl = "/api/v1",
	fetchImpl,
	initialUri,
	initialCatalog,
	initialSchema,
	initialCredentialsRef,
	initialTables,
	initialFingerprint,
	onApply,
}: CatalogTablePickerModalProps) {
	const isSqlite = sourceType === "sqlite";
	const [uri, setUri] = useState<string>(initialUri ?? "");
	const [catalog, setCatalog] = useState<string>(initialCatalog ?? "");
	const [schema, setSchema] = useState<string>(initialSchema ?? "");
	const [credentialsRef, setCredentialsRef] = useState<string>(initialCredentialsRef ?? "");

	const [tables, setTables] = useState<InspectedTable[]>(() => initialTables ?? []);
	const [selectedTableNames, setSelectedTableNames] = useState<Set<string>>(() => new Set((initialTables ?? []).map((t) => t.table_name)));
	const [expandedTables, setExpandedTables] = useState<Set<string>>(new Set());
	const [searchQuery, setSearchQuery] = useState<string>("");
	const [isLoading, setIsLoading] = useState<boolean>(false);
	const [fingerprint, setFingerprint] = useState<string | null>(() => initialFingerprint ?? null);
	const [inspectError, setInspectError] = useState<string | null>(null);

	useEffect(() => {
		setUri(initialUri ?? "");
		setCatalog(initialCatalog ?? "");
		setSchema(initialSchema ?? "");
		setCredentialsRef(initialCredentialsRef ?? "");
		setTables(initialTables ?? []);
		setSelectedTableNames(new Set((initialTables ?? []).map((t) => t.table_name)));
		setExpandedTables(new Set());
		setSearchQuery("");
		setIsLoading(false);
		setInspectError(null);
		setFingerprint(initialFingerprint ?? null);
	}, [sourceType]);

	useEffect(() => {
		if (initialUri !== undefined) setUri(initialUri);
		if (initialCatalog !== undefined) setCatalog(initialCatalog);
		if (initialSchema !== undefined) setSchema(initialSchema);
		if (initialCredentialsRef !== undefined) setCredentialsRef(initialCredentialsRef);
	}, [initialUri, initialCatalog, initialSchema, initialCredentialsRef]);

	useEffect(() => {
		if (!isOpen) return;
		if (initialTables) {
			setTables(initialTables);
			setSelectedTableNames(new Set(initialTables.map((t) => t.table_name)));
			if (initialFingerprint) setFingerprint(initialFingerprint);
			setIsLoading(false);
			return;
		}

		if (!uri || !uri.trim()) {
			setTables([]);
			setSelectedTableNames(new Set());
			setIsLoading(false);
			return;
		}

		let isMounted = true;
		const ctrl = typeof AbortController !== "undefined" ? new AbortController() : null;
		setIsLoading(true);
		setInspectError(null);
		const fetcher = fetchImpl ?? (typeof fetch !== "undefined" ? fetch : null);

		if (!fetcher) {
			setIsLoading(false);
			return;
		}

		fetcher(`${apiUrl}/sources/inspect`, {
			method: "POST",
			credentials: "include",
			headers: getStudioAuthHeaders({ "Content-Type": "application/json" }),
			signal: ctrl ? ctrl.signal : null,
			body: JSON.stringify({
				uri,
				adapter: sourceType,
				namespace: { catalog, schema_name: schema },
				...(credentialsRef.trim() ? { credentials_ref: credentialsRef.trim() } : {}),
			}),
		})
			.then(async (r) => {
				if (!r.ok) {
					const errText = await r.text().catch(() => "");
					throw new Error(`Inspection failed: HTTP ${r.status} - ${errText}`);
				}
				return r.json();
			})
			.then((d) => {
				if (!isMounted) return;
				const raw = (d?.tables ?? []) as any[];
				const mapped: InspectedTable[] = raw.map((t) => ({
					table_name: String(t.table_name ?? t.name ?? "table"),
					estimated_rows: (t.row_count_estimate ?? t.estimated_rows) as number | undefined,
					row_count_estimate: (t.row_count_estimate ?? t.estimated_rows) as number | undefined,
					columns: (t.columns ?? []).map((c: any) => ({
						name: String(c.name ?? c.column_name ?? "col"),
						data_type: String(c.data_type ?? c.type ?? "VARCHAR"),
						physical_type: c.physical_type,
						is_primary_key: Boolean(c.is_primary_key || c.is_pk),
						primary_key_ordinal: c.primary_key_ordinal,
						foreign_key_ref: c.foreign_key_ref ? String(c.foreign_key_ref) : null,
						governance_tags: (c.governance_tags ?? {}) as Record<string, string | boolean>,
					})),
				}));
				setTables(mapped);
				setSelectedTableNames(new Set(mapped.map((t) => t.table_name)));
				setFingerprint((d?.fingerprint as string | undefined) ?? null);
				setIsLoading(false);
			})
			.catch((err) => {
				if (!isMounted) return;
				setInspectError(err instanceof Error ? err.message : String(err));
				setIsLoading(false);
			});

		return () => {
			isMounted = false;
			if (ctrl) ctrl.abort();
		};
	}, [isOpen, uri, catalog, schema, credentialsRef, sourceType, apiUrl, fetchImpl, initialTables, initialFingerprint]);

	const filtered = useMemo(() => {
		if (!searchQuery.trim()) return tables;
		const q = searchQuery.toLowerCase();
		return tables.filter(
			(t) =>
				t.table_name.toLowerCase().includes(q) ||
				t.columns?.some((c) => c.name.toLowerCase().includes(q)),
		);
	}, [tables, searchQuery]);

	const allSelected = filtered.length > 0 && filtered.every((t) => selectedTableNames.has(t.table_name));

	const handleToggleSelectAll = () => {
		setSelectedTableNames((prev) => {
			const next = new Set(prev);
			if (allSelected) filtered.forEach((t) => next.delete(t.table_name));
			else filtered.forEach((t) => next.add(t.table_name));
			return next;
		});
	};

	const toggle = (s: Set<string>, val: string) => {
		const next = new Set(s);
		if (next.has(val)) next.delete(val);
		else next.add(val);
		return next;
	};

	const handleToggleTable = (name: string) => setSelectedTableNames((prev) => toggle(prev, name));
	const handleToggleExpand = (name: string) => setExpandedTables((prev) => toggle(prev, name));

	const handleApply = () => {
		if (selectedTableNames.size === 0) return;
		const selectedList = Array.from(selectedTableNames);
		const clean = (s: string) => s.toLowerCase().replace(/[^a-z0-9_]/g, "_").replace(/^_+|_+$/g, "");
		let sys = "";
		if (sourceType === "sqlite") {
			const base = uri.split(/[/\\]/).pop()?.replace(/\.[^.]+$/, "") || "store";
			sys = clean(`sqlite_${base}_${schema || "main"}`);
		} else if (sourceType === "databricks") {
			const parts = [catalog, schema].filter(Boolean).map((p) => clean(p!));
			sys = parts.length > 0 ? clean(`databricks_${parts.join("_")}`) : clean(`databricks_${uri || "feed"}`);
		} else {
			const base = uri.split(/[/\\]/).pop()?.replace(/\.[^.]+$/, "") || sourceType;
			sys = clean(`${sourceType}_${base}`);
		}
		onApply({
			feed_id: `feed_${sourceType}_${Date.now()}`,
			source_system: sys,
			uri,
			adapter: sourceType,
			namespace: { catalog, schema_name: schema },
			table_selection: { mode: "EXPLICIT", tables: selectedList },
			...(credentialsRef.trim() ? { credentials_ref: credentialsRef.trim() } : {}),
			options: {
				fingerprint: fingerprint ?? null,
				selected_tables_count: selectedList.length,
			},
		});
		onClose();
	};

	if (!isOpen) return null;

	return (
		<div role="dialog" aria-modal="true" aria-labelledby="catalog-picker-title" data-testid="catalog-table-picker-modal" style={S.overlay} onClick={(e) => { if (e.target === e.currentTarget) onClose(); }}>
			<div style={S.panel}>
				<header style={S.header}>
					<div style={{ ...S.row, gap: 8 }}>
						<span data-testid="picker-adapter-badge" style={badgeStyle(isSqlite)}>
							{sourceType === "sqlite" ? "SQLITE" : sourceType === "databricks" ? "DATABRICKS UC" : String(sourceType).toUpperCase()}
						</span>
						<h3 id="catalog-picker-title" style={{ margin: 0, fontSize: 14, fontWeight: 700 }}>Inspect & Pick Catalog Tables</h3>
					</div>
					<button type="button" data-testid="btn-close-modal" aria-label="Close modal" onClick={onClose} style={S.closeBtn}>✕</button>
				</header>

				<div style={S.subbar}>
					<div style={{ ...S.between, fontSize: 12 }}>
						<div>
							<strong>Namespace:</strong>{" "}
							<span data-testid="picker-namespace-badge" style={{ fontFamily: F.mono, color: "#2563EB" }}>
								{isSqlite ? (catalog || "main") : catalog && schema ? `${catalog}.${schema}` : (catalog || schema || "default")}
							</span>
							{fingerprint && (
								<span style={{ marginLeft: 8, color: C.inkMuted }}>
									FP: <span data-testid="picker-fingerprint-badge" style={{ fontFamily: F.mono }}>{fingerprint}</span>
								</span>
							)}
						</div>
						<div data-testid="picker-selection-count" style={{ fontWeight: 600 }}>{selectedTableNames.size} of {tables.length} selected</div>
					</div>
					<div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
						<input type="text" data-testid="input-picker-uri" value={uri} onChange={(e) => setUri(e.target.value)} placeholder="URI" style={{ ...S.input, flex: 2, minWidth: 160, fontFamily: F.mono }} />
						<input type="text" data-testid="input-picker-catalog" value={catalog} onChange={(e) => setCatalog(e.target.value)} placeholder="Catalog" style={{ ...S.input, width: 90, fontFamily: F.mono }} />
						<input type="text" data-testid="input-picker-schema" value={schema} onChange={(e) => setSchema(e.target.value)} placeholder="Schema" style={{ ...S.input, width: 90, fontFamily: F.mono }} />
						<input type="text" data-testid="input-picker-credentials-ref" value={credentialsRef} onChange={(e) => setCredentialsRef(e.target.value)} placeholder="Credentials Ref" style={{ ...S.input, width: 110, fontFamily: F.mono }} />
						<input type="text" data-testid="input-table-search" value={searchQuery} onChange={(e) => setSearchQuery(e.target.value)} placeholder="Filter..." style={{ ...S.input, width: 110 }} />
					</div>
				</div>

				<div style={S.body}>
					{inspectError && (
						<div data-testid="picker-error-banner" style={{ padding: "8px 12px", backgroundColor: "#FEF2F2", color: "#991B1B", border: "1px solid #FECACA", borderRadius: 4, fontSize: 12, marginBottom: 8 }}>
							Inspection Error: {inspectError}
						</div>
					)}

					<div style={S.tblHdr}>
						<input type="checkbox" id="checkbox-select-all-tables" data-testid="checkbox-select-all-tables" checked={allSelected} disabled={isLoading || filtered.length === 0} onChange={handleToggleSelectAll} style={{ cursor: "pointer" }} />
						<label htmlFor="checkbox-select-all-tables" style={{ cursor: "pointer", flex: 1 }}>Select All User Tables ({filtered.length})</label>
					</div>

					{isLoading && <div data-testid="picker-loading-indicator" style={S.loading}>Inspecting schema...</div>}
					{!isLoading && !inspectError && filtered.length === 0 && (
						<div data-testid="picker-empty-state" style={{ padding: 24, textAlign: "center", color: C.inkMuted, fontSize: 12 }}>No tables found.</div>
					)}

					{!isLoading && filtered.map((t) => {
						const isChecked = selectedTableNames.has(t.table_name);
						const isExpanded = expandedTables.has(t.table_name);
						const rows = t.row_count_estimate ?? t.estimated_rows;
						return (
							<div key={t.table_name} data-testid={`table-row-${t.table_name}`} style={rowWrapStyle(isChecked)}>
								<div style={{ ...S.between, padding: "8px 10px" }}>
									<div style={{ ...S.row, gap: 8 }}>
										<input type="checkbox" data-testid={`checkbox-table-${t.table_name}`} checked={isChecked} onChange={() => handleToggleTable(t.table_name)} style={{ cursor: "pointer" }} />
										<span data-testid={`table-name-${t.table_name}`} style={{ fontWeight: 600, fontSize: 12, fontFamily: F.mono }}>{t.table_name}</span>
										{rows !== undefined && <span data-testid={`table-rows-${t.table_name}`} style={S.rowEst}>~{(rows / 1000).toFixed(1)}k rows</span>}
									</div>
									<button type="button" data-testid={`btn-expand-table-${t.table_name}`} onClick={() => handleToggleExpand(t.table_name)} style={S.btn}>
										{isExpanded ? "Hide ▲" : `${t.columns?.length ?? 0} cols ▼`}
									</button>
								</div>
								{isExpanded && t.columns && (
									<div data-testid={`columns-table-${t.table_name}`} style={S.colPanel}>
										<table style={S.tbl}>
											<tbody>
												{t.columns.map((colItem) => (
													<tr key={colItem.name} style={{ borderTop: "1px solid #F1F5F9" }}>
														<td style={S.tdName}>{colItem.name}</td>
														<td style={S.tdType}>{colItem.physical_type ?? colItem.data_type}</td>
														{renderKeyBadges(colItem)}
														{renderTagBadges(colItem)}
													</tr>
												))}
											</tbody>
										</table>
									</div>
								)}
							</div>
						);
					})}
				</div>

				<footer style={S.footer}>
					<button type="button" data-testid="btn-picker-cancel" onClick={onClose} style={S.btn}>Cancel</button>
					<button type="button" data-testid="btn-add-selected-tables" onClick={handleApply} disabled={selectedTableNames.size === 0} style={applyBtnStyle(selectedTableNames.size === 0)}>
						Add Selected Tables ({selectedTableNames.size})
					</button>
				</footer>
			</div>
		</div>
	);
}
