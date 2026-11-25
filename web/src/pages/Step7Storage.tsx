import { useCallback, useEffect, useMemo, useState } from "react";
import { api, parseProblemDetails } from "../api/client";
import {
	type DbtSqlDialect,
	type EmitterDefectReport,
	type MigrationApplyResp,
	STATIC_11_TARGETS,
	type SqlQueryResult,
	type TargetManifest,
	type VerifierDiagnostic,
} from "../api/openapi";
import { useProjectStore } from "../store/projectStore";

type TargetId = (typeof STATIC_11_TARGETS)[number]["target_id"];
const DIALECTS: readonly DbtSqlDialect[] = [
	"duckdb",
	"databricks",
	"snowflake",
	"postgres",
	"bigquery",
	"tsql",
	"redshift",
	"clickhouse",
];

export function Step7Storage() {
	const proj = useProjectStore((s) => s.activeProject);
	const url = useProjectStore((s) => s.currentUrl);
	const vaultIr = useProjectStore((s) => s.vaultIr);
	const run6 = useProjectStore((s) => s.activeStep6Run);
	const step6Rev = useProjectStore((s) => s.activeStep6OntologyRevId);
	const superseded = useProjectStore((s) => s.supersededRunIds);
	const failedVerifyWave = useProjectStore((s) => s.failedVerifyWaveId);
	const sqlQuery = useProjectStore((s) => s.sqlQuery);
	const setSql = useProjectStore((s) => s.setSqlQuery);
	const setIr = useProjectStore((s) => s.setVaultIr);
	const setFailedVerify = useProjectStore((s) => s.setFailedVerifyWaveId);
	const verifyVault = useProjectStore((s) => s.verifyVaultAction);
	const deriveGate = useProjectStore((s) => s.deriveStep7Gate);
	const openExportModal = useProjectStore((s) => s.openExportImportModal);
	const navigate = useProjectStore((s) => s.navigate);
	const setErr = useProjectStore((s) => s.setLastProblem);

	const parsed = new URL(url, "http://localhost");
	const pid =
		proj?.project_id ?? /^\/projects\/([^/]+)/.exec(parsed.pathname)?.[1] ?? "";

	const [manifests, setManifests] = useState<TargetManifest[]>([]);
	const [targetId, setTargetId] = useState<TargetId>("duckdb_local");
	const [dialect, setDialect] = useState<DbtSqlDialect>(
		proj?.settings_snapshot.dbt_sql_dialect ?? "duckdb",
	);
	const [verifyRt, setVerifyRt] = useState(false);
	const [defects, setDefects] = useState<Record<string, EmitterDefectReport>>(
		{},
	);
	const [verifyDiags, setVerifyDiags] = useState<VerifierDiagnostic[]>([]);
	const [migration, setMigration] = useState<MigrationApplyResp | null>(null);
	const [queryRes, setQueryRes] = useState<SqlQueryResult | null>(null);
	const [busy, setBusy] = useState(false);
	const [mobile, setMobile] = useState(
		() => typeof window !== "undefined" && window.innerWidth < 768,
	);

	useEffect(() => {
		if (typeof window === "undefined") return;
		const fn = () => setMobile(window.innerWidth < 768);
		window.addEventListener("resize", fn);
		return () => window.removeEventListener("resize", fn);
	}, []);

	const refresh = useCallback(
		async (sig?: AbortSignal) => {
			if (!pid) return;
			const list = await api.listStorageTargets(pid, sig);
			if (!sig?.aborted) setManifests(list);
		},
		[pid],
	);

	useEffect(() => {
		if (!pid) return;
		const ac = new AbortController();
		if (proj?.active_wave_id && !vaultIr) {
			void api
				.getVaultIr(pid, ac.signal)
				.then((ir) => !ac.signal.aborted && setIr(ir))
				.catch(() => undefined);
		}
		void refresh(ac.signal).catch(
			(err) => !ac.signal.aborted && setErr(parseProblemDetails(err)),
		);
		return () => ac.abort();
	}, [pid, proj?.active_wave_id, vaultIr, setIr, refresh, setErr]);

	const gate = deriveGate();
	const staleOnt = Boolean(
		vaultIr &&
			proj?.active_ontology_rev_id &&
			step6Rev &&
			step6Rev !== proj.active_ontology_rev_id,
	);
	const step6Failed =
		run6?.status === "FAILED" && !superseded.includes(run6.run_id);
	const verifyFailed =
		Boolean(proj?.active_wave_id) && failedVerifyWave === proj?.active_wave_id;
	const locked = !gate.unlocked || staleOnt || busy;

	const manMap = new Map(manifests.map((m) => [m.target_id, m]));
	const selMeta =
		STATIC_11_TARGETS.find((t) => t.target_id === targetId) ??
		STATIC_11_TARGETS[0];
	const selMan = manMap.get(targetId) ?? null;
	const duckReady = manMap.get("duckdb_local")?.last_materialized_wave != null;

	const onCompileErr = (err: unknown, swallow = false) => {
		const p = parseProblemDetails(err);
		if (p.error_code === "EMITTER_VERIFICATION_FAILED" && p.defect_report) {
			const dr = p.defect_report;
			setDefects((prev) => ({ ...prev, [dr.target_id]: dr }));
		} else if (p.error_code === "VERIFICATION_GATE_FAILED") {
			setFailedVerify(proj?.active_wave_id ?? null);
			setVerifyDiags(p.diagnostics ?? []);
		} else if (!swallow) setErr(p);
	};

	const withBusy = async (fn: () => Promise<void>) => {
		setBusy(true);
		try {
			await fn();
		} catch (err) {
			setErr(parseProblemDetails(err));
		} finally {
			setBusy(false);
		}
	};

	const compile = (id: string) =>
		!pid || locked
			? Promise.resolve()
			: withBusy(async () => {
					try {
						await api.compileStorageTarget(pid, {
							target_id: id,
							dbt_sql_dialect: dialect,
							verify_runtime: verifyRt,
						});
						setDefects((prev) => {
							if (id === "all") return {};
							const next = { ...prev };
							delete next[id];
							return next;
						});
						await refresh();
					} catch (err) {
						onCompileErr(err);
						await refresh().catch(() => undefined);
					}
				});

	const compileRemaining = () =>
		!pid || locked
			? Promise.resolve()
			: withBusy(async () => {
					for (const t of STATIC_11_TARGETS) {
						if (defects[t.target_id]) continue;
						try {
							await api.compileStorageTarget(pid, {
								target_id: t.target_id,
								dbt_sql_dialect: dialect,
								verify_runtime: verifyRt,
							});
						} catch (err) {
							onCompileErr(err, true);
						}
					}
					await refresh();
				});

	const qRows = useMemo(() => {
		if (!queryRes) return [];
		const seen = new Map<string, number>();
		return queryRes.rows.map((row) => {
			const sig = row.map(String).join("\0");
			const n = (seen.get(sig) ?? 0) + 1;
			seen.set(sig, n);
			return {
				key: `${sig}#${n}`,
				cells: queryRes.columns.map((col, i) => ({
					col,
					text: row[i] === null ? "NULL" : String(row[i]),
				})),
			};
		});
	}, [queryRes]);

	const defectList = Object.values(defects);
	const goStep6 = () => navigate(`/projects/${encodeURIComponent(pid)}/step/6`);
	const gateAlert =
		!proj?.active_wave_id || !vaultIr
			? {
					cls: "callout-amber",
					title: "Pre-Seal:",
					msg: "Step 6 has not certified a wave yet.",
					cta: "Go to Step 6 →",
				}
			: staleOnt
				? {
						cls: "callout-coral",
						title: "Stale Wave:",
						msg: `Ontology ${proj.active_ontology_rev_id} is newer than certified wave.`,
						cta: "Re-run Step 6 →",
					}
				: step6Failed
					? {
							cls: "banner banner-danger",
							title: "Step 6 Failed:",
							msg: "Latest Step 6 run failed.",
							cta: "Return to Step 6",
						}
					: null;

	return (
		<div className="stack-md">
			<div className="row-between">
				<div>
					<h1 className="h1-serif">
						Step 7 · 11-Target Storage Compilation &amp; DuckDB Runner
					</h1>
					<p className="wb-sub">
						Compile disposable SQL/format bundles under <code>targets/</code>{" "}
						without mutating <code>project.sqlite</code>.
					</p>
				</div>
				<div className="row">
					<label className="row text-sm">
						<span>dbt Dialect:</span>
						<select
							className="select"
							style={{ width: "auto" }}
							value={dialect}
							onChange={(e) => setDialect(e.target.value as DbtSqlDialect)}
						>
							{DIALECTS.map((d) => (
								<option key={d} value={d}>
									{d}
								</option>
							))}
						</select>
					</label>
					<label className="radio-row text-sm">
						<input
							type="checkbox"
							checked={verifyRt}
							onChange={(e) => setVerifyRt(e.target.checked)}
						/>
						<span>verify_runtime (DuckDB oracle)</span>
					</label>
				</div>
			</div>

			{gateAlert && (
				<output className={`${gateAlert.cls} row-between`}>
					<span>
						<strong>{gateAlert.title}</strong> {gateAlert.msg}
					</span>
					<button type="button" className="btn btn-sm" onClick={goStep6}>
						{gateAlert.cta}
					</button>
				</output>
			)}

			{!gateAlert && verifyFailed && (
				<div className="banner banner-danger stack-sm" role="alert">
					<div className="row-between">
						<span>
							<strong>Verification Failed:</strong> Wave {proj?.active_wave_id}{" "}
							failed Verify Vault.
						</span>
						<div className="row">
							<button
								type="button"
								className="btn btn-primary btn-sm"
								disabled={busy}
								onClick={() =>
									void withBusy(async () =>
										setVerifyDiags((await verifyVault(pid)).diagnostics ?? []),
									)
								}
							>
								✓ Verify Vault
							</button>
							<button type="button" className="btn btn-sm" onClick={goStep6}>
								Step 6
							</button>
						</div>
					</div>
					{verifyDiags.map((d) => (
						<div key={`${d.rule_id}-${d.message}`} className="text-sm mono">
							{d.rule_id}: {d.message}
						</div>
					))}
				</div>
			)}

			{defectList.length > 0 && (
				<div className="banner banner-danger stack-sm" role="alert">
					<div className="row-between">
						<strong>
							EMITTER_VERIFICATION_FAILED ({defectList.length} defect
							{defectList.length === 1 ? "" : "s"})
						</strong>
						<button
							type="button"
							className="btn btn-sm"
							disabled={locked}
							onClick={() => void compileRemaining()}
						>
							Compile Remaining Targets Individually
						</button>
					</div>
					{defectList.map((dr) => (
						<div key={dr.target_id} className="card-subtle stack-sm">
							<div className="mono text-sm">
								<strong>{dr.target_id}</strong> · {dr.stage} · {dr.rule_id}:{" "}
								{dr.message}
							</div>
							{dr.sql_or_artifact_snippet && (
								<pre className="card-dark mono text-sm" style={{ margin: 0 }}>
									{dr.sql_or_artifact_snippet}
								</pre>
							)}
						</div>
					))}
				</div>
			)}

			<div className="split-grid" style={{ gridTemplateColumns: "1.08fr 1fr" }}>
				<section className="card-subtle stack-md">
					<div className="row-between">
						<h2 className="h2-serif">11 Warehouse &amp; Format Targets</h2>
						<div className="row">
							<button
								type="button"
								className="btn btn-primary btn-sm"
								disabled={locked}
								onClick={() => void compile(targetId)}
							>
								Compile Selected ({targetId})
							</button>
							<button
								type="button"
								className="btn btn-secondary btn-sm"
								disabled={locked}
								onClick={() => void compile("all")}
							>
								Compile All 11 Targets
							</button>
						</div>
					</div>

					<div className="stack-sm">
						{STATIC_11_TARGETS.map((m) => {
							const man = manMap.get(m.target_id);
							const def = defects[m.target_id];
							return (
								<button
									key={m.target_id}
									type="button"
									className={
										m.target_id === targetId
											? "card-selected row-between"
											: "card-cream row-between"
									}
									style={{ textAlign: "left", width: "100%" }}
									onClick={() => setTargetId(m.target_id)}
								>
									<div>
										<div className="font-semibold">
											{m.display_name}{" "}
											<span className="mono text-sm text-muted">
												({m.target_id})
											</span>
										</div>
										<div className="text-sm text-muted">{m.summary}</div>
									</div>
									<div className="row">
										<span
											className={`pill ${def ? "pill-err" : man ? "pill-ok" : ""}`}
										>
											{def
												? "Defect"
												: man
													? `${man.certification_status} · W${man.last_compiled_wave}`
													: "Not Compiled"}
										</span>
										<span className="pill">
											{m.verification_tier === "RUNTIME_VERIFIED"
												? "Runtime"
												: "Syntax/Contract"}
										</span>
									</div>
								</button>
							);
						})}
					</div>

					<div className="row">
						<button
							type="button"
							className="btn btn-primary btn-sm"
							disabled={locked || !selMeta?.supports_migrate}
							onClick={() =>
								!locked &&
								selMeta?.supports_migrate &&
								void withBusy(async () => {
									setMigration(
										await api.migrateStorageTarget(pid, {
											target_id: targetId,
										}),
									);
									await refresh();
								})
							}
						>
							Migrate Selected Target ({targetId})
						</button>
						<button
							type="button"
							className="btn btn-secondary btn-sm"
							disabled={locked}
							onClick={() =>
								!locked &&
								void withBusy(async () => {
									setMigration(
										await api.regenerateStorageTarget(pid, {
											target_id: "duckdb_local",
										}),
									);
									await refresh();
								})
							}
						>
							Reset &amp; Test Full Regen (duckdb_local)
						</button>
					</div>

					{migration && (
						<output className="banner banner-ok text-sm">
							Target <strong>{migration.target_id}</strong> wave{" "}
							{migration.wave_number}: rows_loaded={migration.rows_loaded} ·
							verified=
							{String(migration.verified)}
						</output>
					)}
				</section>

				<section className="stack-md">
					<div className="card-subtle stack-sm">
						<h2 className="h2-serif">TargetEvalGate Receipts · {targetId}</h2>
						{!selMan ? (
							<p className="text-muted text-sm">Target not compiled yet.</p>
						) : (
							<div className="stack-sm text-sm">
								<div className="row">
									<span className="pill pill-ok">
										{selMan.certification_status}
									</span>
									<span className="pill mono">
										Wave {selMan.last_compiled_wave}
									</span>
									{selMan.last_materialized_wave != null && (
										<span className="pill pill-sal">
											Materialized W{selMan.last_materialized_wave}
										</span>
									)}
								</div>
								<div>
									<strong>Eval Receipts:</strong>{" "}
									{(selMan.eval_receipts ?? []).join(", ") || "None"}
								</div>
								<div>
									<strong>Generated Files:</strong>{" "}
									<span className="mono">
										{(selMan.generated_files ?? []).join(", ")}
									</span>
								</div>
							</div>
						)}
					</div>

					<div className="card-dark stack-sm">
						<h2 className="h2-serif" style={{ color: "#faf9f5" }}>
							Sandboxed Read-Only DuckDB Query Runner
						</h2>
						<div className="banner banner-warn text-sm">
							Loopback DuckDB runner: <code>sat_*_pii</code> returns unmasked
							rows with <code>-1</code>/<code>-2</code> Dual Zero Keys and{" "}
							<code>1900-01-01T00:00:00Z</code> Ghost records.
						</div>
						{!duckReady && (
							<div className="text-sm" style={{ color: "#a09d96" }}>
								Run <strong>Migrate Selected Target (duckdb_local)</strong>{" "}
								before querying.
							</div>
						)}
						{mobile ? (
							<pre
								className="card-dark-inner mono text-sm"
								style={{ margin: 0, overflowX: "auto" }}
							>
								{sqlQuery ||
									"SELECT * FROM vw_custody_position_current LIMIT 10;"}
							</pre>
						) : (
							<form
								className="stack-sm"
								onSubmit={(e) => {
									e.preventDefault();
									if (pid && duckReady && sqlQuery.trim() && !busy) {
										void withBusy(async () =>
											setQueryRes(
												await api.queryStorage(pid, {
													sql: sqlQuery.trim(),
													limit: 1000,
												}),
											),
										);
									}
								}}
							>
								<textarea
									rows={4}
									aria-label="Read-only DuckDB SQL Query"
									className="textarea mono"
									style={{
										background: "#1f1e1b",
										color: "#faf9f5",
										borderColor: "#33312d",
									}}
									placeholder="SELECT * FROM hub_account LIMIT 25;"
									value={sqlQuery}
									onChange={(e) => setSql(e.target.value)}
								/>
								<div className="row-end">
									<button
										type="submit"
										className="btn btn-coral-fill btn-sm"
										disabled={busy || !duckReady || !sqlQuery.trim()}
									>
										Run Query
									</button>
								</div>
							</form>
						)}
						{queryRes && (
							<div className="table-scroll-region">
								<table className="responsive-table">
									<thead>
										<tr>
											{queryRes.columns.map((c) => (
												<th key={c} scope="col">
													{c}
												</th>
											))}
										</tr>
									</thead>
									<tbody>
										{qRows.map((r) => (
											<tr key={r.key}>
												{r.cells.map((cell) => (
													<td
														key={cell.col}
														className="mono"
														data-label={cell.col}
													>
														{cell.text}
													</td>
												))}
											</tr>
										))}
									</tbody>
								</table>
							</div>
						)}
					</div>
				</section>
			</div>

			<div className="card-subtle row-between" style={{ minHeight: 52 }}>
				<button
					type="button"
					className="btn btn-secondary"
					onClick={() =>
						navigate(`/projects/${encodeURIComponent(pid)}/explorer?view=3`)
					}
				>
					Inspect IR &amp; Target Manifests (Explorer View 3) ↗
				</button>
				<button
					type="button"
					className="btn btn-primary"
					onClick={() => openExportModal(null)}
				>
					Export Portable .opendv.zip Bundle
				</button>
			</div>
		</div>
	);
}
