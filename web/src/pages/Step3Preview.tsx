import { useEffect, useMemo, useRef, useState } from "react";
import { api, parseProblemDetails } from "../api/client";
import type {
	ConceptMemorySpec,
	ProblemDetails,
	TableIndexEntry,
	TablePreviewPage,
} from "../api/openapi";
import { Step3bScanView } from "../components/Step3bScanView";
import { parseAppRoute, useProjectStore } from "../store/projectStore";

type SubTab = "3a" | "3b";
const SHARD_RE = /^(.+)_[0-9]{4,8}$/;
const PAGE_LIMIT = 50;
const A: Record<string, Record<string, unknown>> = {
	s: { tabIndex: 0 },
	t: { role: "table" },
	r: { role: "row" },
	c: { role: "cell" },
};
const MEM_CSS: Record<string, React.CSSProperties> = {
	PINNED: {
		backgroundColor: "var(--primary-active)",
		color: "var(--on-primary)",
		borderColor: "var(--primary-active)",
	},
	MUTED: {
		textDecoration: "line-through",
		backgroundColor: "var(--surface-soft)",
		color: "var(--muted)",
	},
};

const tok = (s: string) =>
	s
		.replace(/([a-z0-9])([A-Z])/g, "$1 $2")
		.split(/[^A-Za-z0-9]+/)
		.map((t) => t.trim().toLowerCase())
		.filter((t) => t.length >= 2);

function groupTables(tables: TableIndexEntry[]) {
	const cm = new Map<string, Map<string, TableIndexEntry[]>>();
	for (const e of tables) {
		const b = SHARD_RE.exec(e.table_name.trim())?.[1] ?? e.table_name;
		const gm = cm.get(e.cluster_id) ?? new Map<string, TableIndexEntry[]>();
		gm.set(b, [...(gm.get(b) ?? []), e]);
		cm.set(e.cluster_id, gm);
	}
	return [...cm.entries()].map(([id, g]) => [id, [...g.entries()]] as const);
}

export function Step3Preview() {
	const {
		currentUrl,
		activeProject,
		sources,
		documentMaps,
		setSources,
		setDocumentMaps,
		navigate,
	} = useProjectStore();
	const parsed = useMemo(() => {
		const u = new URL(currentUrl, "http://localhost");
		return parseAppRoute(u.pathname, u.search);
	}, [currentUrl]);
	const projectId = activeProject?.project_id ?? parsed.projectId;
	const sub: SubTab = parsed.query.get("sub") === "3b" ? "3b" : "3a";

	const tab3aRef = useRef<HTMLButtonElement>(null);
	const tab3bRef = useRef<HTMLButtonElement>(null);
	const [tables, setTables] = useState<TableIndexEntry[]>([]);
	const [mem, setMem] = useState<ConceptMemorySpec[]>([]);
	const [openShards, setOpenShards] = useState<Record<string, boolean>>({});
	const [selTable, setSelTable] = useState<TableIndexEntry | null>(null);
	const [offset, setOffset] = useState(0);
	const [preview, setPreview] = useState<TablePreviewPage | null>(null);
	const [loadingTbl, setLoadingTbl] = useState(false);
	const [problem, setProblem] = useState<ProblemDetails | null>(null);

	useEffect(() => {
		if (!projectId) return;
		let ok = true;
		setProblem(null);
		void Promise.all([
			api.listTablesIndex(projectId),
			api.getConceptMemory(projectId),
			api.listDocumentMaps(projectId),
			sources.length === 0 ? api.listSources(projectId) : Promise.resolve(null),
		])
			.then(([t, m, d, s]) => {
				if (!ok) return;
				setTables(t);
				setMem(m);
				setDocumentMaps(d);
				if (s) setSources(s);
			})
			.catch((e) => ok && setProblem(parseProblemDetails(e)));
		return () => {
			ok = false;
		};
	}, [projectId, sources.length, setDocumentMaps, setSources]);

	const clusters = useMemo(() => groupTables(tables), [tables]);
	const memMap = useMemo(
		() => new Map(mem.map((r) => [r.term.toLowerCase(), r])),
		[mem],
	);
	const terms = useMemo(() => {
		const seen = new Set(mem.map((r) => r.term.toLowerCase()));
		const out = mem.map((r) => r.term);
		const strs = tables
			.flatMap((t) => [
				t.table_name,
				...(t.pk_cols ?? []),
				...(t.fk_refs ?? []),
			])
			.concat(preview?.columns ?? []);
		for (const s of strs) {
			for (const w of tok(s)) {
				if (!seen.has(w) && out.length < 60) {
					seen.add(w);
					out.push(w);
				}
			}
		}
		return out;
	}, [mem, tables, preview]);

	if (!projectId) {
		return (
			<section className="panel stack-md">
				<h1 className="h1-serif">Step 3 · Preview &amp; Clusters</h1>
				<p className="text-muted">Select or create a project in Step 1.</p>
				<button
					type="button"
					className="btn btn-primary"
					onClick={() => navigate("/")}
				>
					Go to Step 1
				</button>
			</section>
		);
	}

	const switchSub = (next: SubTab) =>
		navigate(`/projects/${encodeURIComponent(projectId)}/step/3?sub=${next}`);
	const onTabKey = (e: React.KeyboardEvent<HTMLButtonElement>) => {
		if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(e.key)) return;
		e.preventDefault();
		const next: SubTab =
			e.key === "Home"
				? "3a"
				: e.key === "End"
					? "3b"
					: sub === "3a"
						? "3b"
						: "3a";
		switchSub(next);
		(next === "3a" ? tab3aRef : tab3bRef).current?.focus();
	};

	const loadPage = async (e: TableIndexEntry, off: number) => {
		setSelTable(e);
		setOffset(off);
		setLoadingTbl(true);
		setProblem(null);
		try {
			setPreview(
				await api.previewTable(projectId, e.table_id, {
					offset: off,
					limit: PAGE_LIMIT,
					sourceId: e.source_id,
				}),
			);
		} catch (err) {
			setProblem(parseProblemDetails(err));
		} finally {
			setLoadingTbl(false);
		}
	};

	const cycleTerm = async (term: string) => {
		const cur = memMap.get(term.toLowerCase())?.state ?? "UNSET";
		const state: ConceptMemorySpec["state"] =
			cur === "UNSET" ? "PINNED" : cur === "PINNED" ? "MUTED" : "UNSET";
		try {
			const s = await api.upsertConceptMemory(projectId, {
				term,
				state,
				concept_uri: null,
			});
			setMem((p) => [
				...p.filter((x) => x.term.toLowerCase() !== s.term.toLowerCase()),
				s,
			]);
		} catch (err) {
			setProblem(parseProblemDetails(err));
		}
	};

	const src = selTable
		? sources.find(
				(s) => s.source_id === (preview?.source_id ?? selTable.source_id),
			)
		: undefined;

	const previewCols = preview?.columns ?? [];
	const previewRows = preview?.rows ?? [];
	const renderBtn = (e: TableIndexEntry) => {
		const active =
			selTable?.table_id === e.table_id && selTable.source_id === e.source_id;
		return (
			<button
				key={`${e.source_id}:${e.table_id}`}
				type="button"
				className={
					active ? "card-selected row-between" : "btn btn-sm row-between"
				}
				style={{ width: "100%", textAlign: "left" }}
				onClick={() => void loadPage(e, 0)}
			>
				<span className="mono">
					{e.table_name} ({e.col_count}c)
				</span>
				<span className="row">
					{e.pii_col_count > 0 && (
						<span className="pill pill-pii">{e.pii_col_count} PII cols</span>
					)}
					<span className="pill">
						{e.badge}
						{e.modeled_version ? ` · ${e.modeled_version}` : ""}
					</span>
				</span>
			</button>
		);
	};

	return (
		<section className="stack-md">
			<div className="row-between">
				<div>
					<h1 className="h1-serif">Step 3 · Preview &amp; Clusters</h1>
					<p className="wb-sub">
						Inspect MinHash clusters, curate ConceptMemory tags, and approve
						scanned documents.
					</p>
				</div>
				<button
					type="button"
					className="btn btn-primary"
					onClick={() =>
						navigate(`/projects/${encodeURIComponent(projectId)}/step/4`)
					}
				>
					Proceed to Step 4 Discovery →
				</button>
			</div>

			<div
				className="tab-strip"
				role="tablist"
				aria-label="Step 3 Preview Sections"
			>
				<button
					ref={tab3aRef}
					type="button"
					role="tab"
					id="step3-tab-3a"
					className="tab-btn"
					aria-selected={sub === "3a"}
					aria-controls="step3-tabpanel"
					tabIndex={sub === "3a" ? 0 : -1}
					onClick={() => switchSub("3a")}
					onKeyDown={onTabKey}
				>
					3a Catalog &amp; Clusters ({tables.length})
				</button>
				<button
					ref={tab3bRef}
					type="button"
					role="tab"
					id="step3-tab-3b"
					className="tab-btn"
					aria-selected={sub === "3b"}
					aria-controls="step3-tabpanel"
					tabIndex={sub === "3b" ? 0 : -1}
					onClick={() => switchSub("3b")}
					onKeyDown={onTabKey}
				>
					3b Outlines &amp; Scan Gate ({documentMaps.length})
				</button>
			</div>

			{problem && (
				<div className="banner banner-danger" role="alert">
					<strong>{problem.title}</strong> ({problem.error_code}):{" "}
					{problem.detail}
				</div>
			)}

			<div
				id="step3-tabpanel"
				role="tabpanel"
				aria-labelledby={sub === "3a" ? "step3-tab-3a" : "step3-tab-3b"}
			>
				{sub === "3b" ? (
					<Step3bScanView projectId={projectId} setProblem={setProblem} />
				) : (
					<div className="split-grid">
						<section className="panel stack-sm">
							<h2 className="h2-serif">
								MinHash Cluster Tree ({tables.length} tables · {clusters.length}{" "}
								clusters)
							</h2>
							{clusters.length === 0 ? (
								<p className="text-sm text-muted">No profiled tables yet.</p>
							) : (
								clusters.map(([id, shards]) => (
									<div key={id} className="card-subtle stack-sm">
										<div className="row-between">
											<strong className="mono">{id}</strong>
											<span className="pill">
												{shards.reduce((n, [, l]) => n + l.length, 0)} tables
											</span>
										</div>
										{shards.map(([b, list]) => {
											if (list.length === 1 && list[0])
												return renderBtn(list[0]);
											const k = `${id}:${b}`;
											const open = Boolean(openShards[k]);
											return (
												<div
													key={b}
													className="stack-sm"
													style={{
														borderLeft: "2px solid var(--hairline-strong)",
														paddingLeft: 8,
													}}
												>
													<button
														type="button"
														className="btn btn-sm btn-ghost row-between"
														style={{ width: "100%" }}
														onClick={() =>
															setOpenShards((p) => ({ ...p, [k]: !open }))
														}
													>
														<span className="mono">
															{open ? "▾" : "▸"} {b}_*
														</span>
														<span className="pill">{list.length} shards</span>
													</button>
													{open && list.map(renderBtn)}
												</div>
											);
										})}
									</div>
								))
							)}
						</section>

						<div className="stack-md">
							<section className="panel stack-sm">
								<div className="row-between">
									<h2 className="h2-serif">ConceptMemory Term Cloud</h2>
									<span className="text-sm text-muted">
										Unset → Pinned → Muted
									</span>
								</div>
								<div className="row" style={{ gap: 6 }}>
									{terms.map((t) => {
										const s = memMap.get(t.toLowerCase())?.state ?? "UNSET";
										return (
											<button
												key={t}
												type="button"
												className="pill"
												style={MEM_CSS[s]}
												aria-pressed={s !== "UNSET"}
												data-memory-state={s}
												onClick={() => void cycleTerm(t)}
											>
												{t} ({s})
											</button>
										);
									})}
								</div>
							</section>

							<section className="panel stack-sm">
								<h2 className="h2-serif">
									Table Preview{selTable ? ` · ${selTable.table_name}` : ""}
								</h2>
								{!selTable ? (
									<p className="text-sm text-muted">
										Click any table to load 50-row masked previews.
									</p>
								) : (
									<div className="stack-sm">
										<div className="row">
											<span className="pill pill-pii">
												{selTable.pii_col_count} PII cols
											</span>
											{(selTable.pk_cols ?? []).length > 0 && (
												<span className="pill mono">
													PK: {selTable.pk_cols?.join(", ")}
												</span>
											)}
											{(selTable.fk_refs ?? []).length > 0 && (
												<span className="pill mono">
													FK: {selTable.fk_refs?.join(", ")}
												</span>
											)}
										</div>
										{src?.status === "METADATA_ONLY_BINARY_ABSENT" && (
											<div className="callout-teal">
												Offline preview for{" "}
												<span className="mono">{src.source_id}</span>{" "}
												(METADATA_ONLY_BINARY_ABSENT).
											</div>
										)}
										{loadingTbl ? (
											<p className="text-sm text-muted">Loading rows...</p>
										) : (
											preview && (
												<>
													<div className="table-scroll-region" {...A.s}>
														<table className="responsive-table" {...A.t}>
															<thead>
																<tr {...A.r}>
																	{previewCols.map((c) => (
																		<th key={c} scope="col">
																			{c}
																		</th>
																	))}
																</tr>
															</thead>
															<tbody>
																{previewRows.map((r, ri) => (
																	<tr
																		key={`${preview.table_id}-${offset + ri}`}
																		{...A.r}
																	>
																		{previewCols.map((c, ci) => (
																			<td
																				key={`${c}-${offset + ri}`}
																				{...A.c}
																				data-label={c}
																				className="mono"
																			>
																				{r[ci] ?? "NULL"}
																			</td>
																		))}
																	</tr>
																))}
															</tbody>
														</table>
													</div>
													<div className="row-between text-sm text-muted">
														<span>
															{preview.total_rows > previewRows.length
																? `Truncated sample: ${offset + 1}–${offset + previewRows.length} of ${preview.total_rows} rows.`
																: `${previewRows.length} of ${preview.total_rows} rows.`}
														</span>
														<div className="row">
															<button
																type="button"
																className="btn btn-sm"
																disabled={offset === 0}
																onClick={() =>
																	void loadPage(
																		selTable,
																		Math.max(0, offset - PAGE_LIMIT),
																	)
																}
															>
																Previous 50
															</button>
															<button
																type="button"
																className="btn btn-sm"
																disabled={
																	offset + previewRows.length >=
																	preview.total_rows
																}
																onClick={() =>
																	void loadPage(selTable, offset + PAGE_LIMIT)
																}
															>
																Next 50
															</button>
														</div>
													</div>
												</>
											)
										)}
									</div>
								)}
							</section>
						</div>
					</div>
				)}
			</div>
		</section>
	);
}
