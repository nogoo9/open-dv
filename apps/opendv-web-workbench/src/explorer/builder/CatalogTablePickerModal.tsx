import type { SourceFeedDTO } from "@opendv/protocol-ts";
import { type Dispatch, type SetStateAction, useEffect, useMemo, useRef, useState } from "react";
import { CLAUDE_TOKENS, EXPLORER_FONTS as F } from "../design-tokens";
import {
	PICKER_STYLES as S,
	pickerApplyBtnStyle as applyBtnStyle,
	pickerBadgeStyle as badgeStyle,
	pickerRowWrapStyle as rowWrapStyle,
} from "./catalogPickerStyles";

export interface ForeignKeyHintLike {
	table?: string;
	column?: string;
	target_table?: string;
	target_column?: string;
	target_columns?: string[];
}

export function formatForeignKeyRef(fk: unknown): string | null {
	if (!fk) return null;
	if (typeof fk === "string") return fk.trim() || null;
	if (typeof fk !== "object") return null;
	const r = fk as ForeignKeyHintLike;
	const tbl = (r.target_table ?? r.table)?.trim() ?? "";
	const col =
		(r.target_column ?? r.column)?.trim() ||
		r.target_columns?.[0]?.trim() ||
		"";
	return tbl && col ? `${tbl}.${col}` : tbl || col || null;
}

export interface InspectedColumn {
	name: string;
	data_type: string;
	physical_type?: string;
	is_primary_key?: boolean;
	primary_key_ordinal?: number;
	foreign_key_ref?: string | ForeignKeyHintLike | null;
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
	initialCatalog?: string | undefined;
	initialSchema?: string | undefined;
	initialCredentialsRef?: string | undefined;
	initialTables?: InspectedTable[] | undefined;
	defaultExpandedTables?: string[] | undefined;
	initialFingerprint?: string | undefined;
	onApply: (feed: SourceFeedDTO) => void;
}

const C = CLAUDE_TOKENS.colors;

const renderKeyBadges = (c: InspectedColumn) => {
	const fkStr = formatForeignKeyRef(c.foreign_key_ref);
	return (
		<td style={S.td}>
			{c.is_primary_key && <span data-testid={`pk-badge-${c.name}`} style={S.pk}>PK{c.primary_key_ordinal ? ` (#${c.primary_key_ordinal})` : ""}</span>}
			{fkStr && <span data-testid={`fk-badge-${c.name}`} style={S.fk}>FK ➔ {fkStr}</span>}
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
	defaultExpandedTables,
	initialFingerprint,
	onApply,
}: CatalogTablePickerModalProps) {
	const isSqlite = sourceType === "sqlite";
	const [uri, setUri] = useState(initialUri ?? "");
	const [catalog, setCatalog] = useState(initialCatalog ?? "");
	const [schema, setSchema] = useState(initialSchema ?? "");
	const [credentialsRef, setCredentialsRef] = useState(initialCredentialsRef ?? "");
	const [tables, setTables] = useState<InspectedTable[]>(() => initialTables ?? []);
	const [selectedTableNames, setSelectedTableNames] = useState(() => new Set((initialTables ?? []).map((t) => t.table_name)));
	const [expandedTables, setExpandedTables] = useState(() => new Set(defaultExpandedTables ?? []));
	const [searchQuery, setSearchQuery] = useState("");
	const [isLoading, setIsLoading] = useState(false);
	const [fingerprint, setFingerprint] = useState<string | null>(() => initialFingerprint ?? null);
	const [inspectError, setInspectError] = useState<string | null>(null);
	const dlgRef = useRef<HTMLDialogElement | null>(null);

	useEffect(() => {
		const dlg = dlgRef.current;
		if (!dlg) return;
		if (isOpen) {
			if (!dlg.open && typeof dlg.showModal === "function") {
				dlg.showModal();
			}
		} else if (dlg.open && typeof dlg.close === "function") {
			dlg.close();
		}
	}, [isOpen]);

	useEffect(() => {
		setUri(initialUri ?? "");
		setCatalog(initialCatalog ?? "");
		setSchema(initialSchema ?? "");
		setCredentialsRef(initialCredentialsRef ?? "");
		setTables(initialTables ?? []);
		setSelectedTableNames(new Set((initialTables ?? []).map((t) => t.table_name)));
		setExpandedTables(new Set(defaultExpandedTables ?? []));
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
			headers: { "Content-Type": "application/json" },
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
				const mapped: InspectedTable[] = ((d?.tables ?? []) as any[]).map((t) => {
					const est = (t.row_count_estimate ?? t.estimated_rows) as number | undefined;
					return {
						table_name: String(t.table_name ?? t.name ?? "table"),
						estimated_rows: est,
						row_count_estimate: est,
						columns: (t.columns ?? []).map((c: any) => ({
							name: String(c.name ?? c.column_name ?? "col"),
							data_type: String(c.data_type ?? c.type ?? "VARCHAR"),
							physical_type: c.physical_type,
							is_primary_key: Boolean(c.is_primary_key || c.is_pk),
							primary_key_ordinal: c.primary_key_ordinal,
							foreign_key_ref: formatForeignKeyRef(c.foreign_key_ref),
							governance_tags: (c.governance_tags ?? {}) as Record<string, string | boolean>,
						})),
					};
				});
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
			ctrl?.abort();
		};
	}, [isOpen, uri, catalog, schema, credentialsRef, sourceType, apiUrl, fetchImpl, initialTables, initialFingerprint]);

	const filtered = useMemo(() => {
		const q = searchQuery.trim().toLowerCase();
		if (!q) return tables;
		return tables.filter((t) => t.table_name.toLowerCase().includes(q) || t.columns?.some((c) => c.name.toLowerCase().includes(q)));
	}, [tables, searchQuery]);

	const allSelected = filtered.length > 0 && filtered.every((t) => selectedTableNames.has(t.table_name));
	const handleToggleSelectAll = () => {
		setSelectedTableNames((prev) => {
			const next = new Set(prev);
			filtered.forEach((t) => (allSelected ? next.delete(t.table_name) : next.add(t.table_name)));
			return next;
		});
	};
	const toggleSet = (setter: Dispatch<SetStateAction<Set<string>>>, val: string) =>
		setter((prev) => { const n = new Set(prev); n.has(val) ? n.delete(val) : n.add(val); return n; });

	const handleApply = () => {
		if (selectedTableNames.size === 0) return;
		const selectedList = Array.from(selectedTableNames);
		const clean = (s: string) => s.toLowerCase().replace(/[^a-z0-9_]/g, "_").replace(/^_+|_+$/g, "");
		const base = uri.split(/[/\\]/).pop()?.replace(/\.[^.]+$/, "");
		const ucParts = [catalog, schema].filter(Boolean).map((p) => clean(p!));
		const sys =
			sourceType === "sqlite"
				? clean(`sqlite_${base || "store"}_${schema || "main"}`)
				: sourceType === "databricks"
					? clean(`databricks_${ucParts.length > 0 ? ucParts.join("_") : uri || "feed"}`)
					: clean(`${sourceType}_${base || sourceType}`);
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
		<dialog
			ref={dlgRef}
			open={typeof HTMLDialogElement === "undefined" ? true : undefined}
			onCancel={(e) => {
				e.preventDefault();
				onClose();
			}}
			role="dialog"
			aria-modal="true"
			aria-labelledby="catalog-picker-title"
			data-testid="catalog-table-picker-modal"
			style={S.dialog}
			onClick={(e) => {
				if (e.target === e.currentTarget) onClose();
			}}
		>
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
							<span data-testid="picker-namespace-badge" style={S.nsBadge}>
								{isSqlite ? (catalog || "main") : catalog && schema ? `${catalog}.${schema}` : (catalog || schema || "default")}
							</span>
							{fingerprint && (
								<span style={{ marginLeft: 8, color: C.muted }}>
									FP: <span data-testid="picker-fingerprint-badge" style={{ fontFamily: F.mono }}>{fingerprint}</span>
								</span>
							)}
						</div>
						<div data-testid="picker-selection-count" style={{ fontWeight: 600 }}>{selectedTableNames.size} of {tables.length} selected</div>
					</div>
					<div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
						<input type="text" data-testid="input-picker-uri" value={uri} onChange={(e) => setUri(e.target.value)} placeholder="URI" style={{ ...S.monoInput, flex: 2, minWidth: 160 }} />
						<input type="text" data-testid="input-picker-catalog" value={catalog} onChange={(e) => setCatalog(e.target.value)} placeholder="Catalog" style={{ ...S.monoInput, width: 90 }} />
						<input type="text" data-testid="input-picker-schema" value={schema} onChange={(e) => setSchema(e.target.value)} placeholder="Schema" style={{ ...S.monoInput, width: 90 }} />
						<input type="text" data-testid="input-picker-credentials-ref" value={credentialsRef} onChange={(e) => setCredentialsRef(e.target.value)} placeholder="Credentials Ref" style={{ ...S.monoInput, width: 110 }} />
						<input type="text" data-testid="input-table-search" value={searchQuery} onChange={(e) => setSearchQuery(e.target.value)} placeholder="Filter..." style={{ ...S.input, width: 110 }} />
					</div>
				</div>

				<div style={S.body}>
					{inspectError && <div data-testid="picker-error-banner" style={S.err}>Inspection Error: {inspectError}</div>}

					<div style={S.tblHdr}>
						<input type="checkbox" id="checkbox-select-all-tables" data-testid="checkbox-select-all-tables" checked={allSelected} disabled={isLoading || filtered.length === 0} onChange={handleToggleSelectAll} style={{ cursor: "pointer" }} />
						<label htmlFor="checkbox-select-all-tables" style={{ cursor: "pointer", flex: 1 }}>Select All User Tables ({filtered.length})</label>
					</div>

					{isLoading && <div data-testid="picker-loading-indicator" style={S.loading}>Inspecting schema...</div>}
					{!isLoading && !inspectError && filtered.length === 0 && (
						<div data-testid="picker-empty-state" style={{ padding: 24, textAlign: "center", color: C.muted, fontSize: 12 }}>No tables found.</div>
					)}

					{!isLoading && filtered.map((t) => {
						const isChecked = selectedTableNames.has(t.table_name);
						const isExpanded = expandedTables.has(t.table_name);
						const rows = t.row_count_estimate ?? t.estimated_rows;
						return (
							<div key={t.table_name} data-testid={`table-row-${t.table_name}`} style={rowWrapStyle(isChecked)}>
								<div style={{ ...S.between, padding: "8px 10px" }}>
									<div style={{ ...S.row, gap: 8 }}>
										<input type="checkbox" data-testid={`checkbox-table-${t.table_name}`} checked={isChecked} onChange={() => toggleSet(setSelectedTableNames, t.table_name)} style={{ cursor: "pointer" }} />
										<span data-testid={`table-name-${t.table_name}`} style={{ fontWeight: 600, fontSize: 12, fontFamily: F.mono }}>{t.table_name}</span>
										{rows !== undefined && <span data-testid={`table-rows-${t.table_name}`} style={S.rowEst}>~{(rows / 1000).toFixed(1)}k rows</span>}
									</div>
									<button type="button" data-testid={`btn-expand-table-${t.table_name}`} onClick={() => toggleSet(setExpandedTables, t.table_name)} style={S.btn}>
										{isExpanded ? "Hide ▲" : `${t.columns?.length ?? 0} cols ▼`}
									</button>
								</div>
								{isExpanded && t.columns && (
									<div data-testid={`columns-table-${t.table_name}`} style={S.colPanel}>
										<table style={S.tbl}>
											<tbody>
												{t.columns.map((colItem) => (
													<tr key={colItem.name} style={S.colRow}>
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
		</dialog>
	);
}
