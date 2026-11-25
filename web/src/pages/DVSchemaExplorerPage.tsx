import { Suspense, lazy, useEffect, useMemo, useState } from "react";
import { api, parseProblemDetails } from "../api/client";
import type { TargetManifest } from "../api/openapi";
import {
	type VaultFilterChip,
	buildVaultCanvasGraph,
} from "../components/graphBuilders";
import { useProjectStore } from "../store/projectStore";

const BipartiteGraphCanvas = lazy(() =>
	import("../components/BipartiteGraphCanvas").then((m) => ({
		default: m.BipartiteGraphCanvas,
	})),
);

const VIEWS = [
	["1", "1 Zone Flow"],
	["2", "2 Bipartite Graph"],
	["3", "3 IR & Target Manifests"],
	["4", "4 Wave Diff & Locks"],
] as const;

type ViewId = (typeof VIEWS)[number][0];

export function DVSchemaExplorerPage() {
	const activeProject = useProjectStore((s) => s.activeProject);
	const currentUrl = useProjectStore((s) => s.currentUrl);
	const vaultIr = useProjectStore((s) => s.vaultIr);
	const ontologyBundle = useProjectStore((s) => s.ontologyBundle);
	const setVaultIr = useProjectStore((s) => s.setVaultIr);
	const navigate = useProjectStore((s) => s.navigate);
	const setLastProblem = useProjectStore((s) => s.setLastProblem);

	const parsed = new URL(currentUrl, "http://localhost");
	const rawView = parsed.searchParams.get("view") ?? "1";
	const activeView: ViewId =
		rawView === "2" || rawView === "3" || rawView === "4" ? rawView : "1";
	const pid =
		activeProject?.project_id ??
		/^\/projects\/([^/]+)/.exec(parsed.pathname)?.[1] ??
		"";

	const [manifests, setManifests] = useState<TargetManifest[]>([]);
	const [chip, setChip] = useState<VaultFilterChip>("all");
	const [expandedParents, setExpandedParents] = useState<Set<string>>(
		() => new Set(),
	);
	const [isMobile, setIsMobile] = useState(
		() => typeof window !== "undefined" && window.innerWidth < 768,
	);

	useEffect(() => {
		if (typeof window === "undefined") return;
		const onResize = () => setIsMobile(window.innerWidth < 768);
		window.addEventListener("resize", onResize);
		return () => window.removeEventListener("resize", onResize);
	}, []);

	useEffect(() => {
		if (!pid) return;
		const ac = new AbortController();
		if (activeProject?.active_wave_id && !vaultIr) {
			void api
				.getVaultIr(pid, ac.signal)
				.then((ir) => {
					if (!ac.signal.aborted) setVaultIr(ir);
				})
				.catch((err) => {
					if (!ac.signal.aborted && parseProblemDetails(err).status !== 404) {
						setLastProblem(parseProblemDetails(err));
					}
				});
		}
		void api
			.listStorageTargets(pid, ac.signal)
			.then((list) => {
				if (!ac.signal.aborted) setManifests(list);
			})
			.catch(() => undefined);
		return () => ac.abort();
	}, [pid, activeProject?.active_wave_id, vaultIr, setVaultIr, setLastProblem]);

	const waveChipLabel =
		vaultIr && vaultIr.wave_number >= 2
			? `+W${String(vaultIr.wave_number).padStart(3, "0")}`
			: "W001";

	const CHIPS: readonly { id: VaultFilterChip; label: string }[] = [
		{ id: "all", label: "All Zones" },
		{ id: "z0", label: "Z0 Bronze & Refs" },
		{ id: "z1", label: "Z1 Stg" },
		{ id: "z2_hubs", label: "Z2 Hubs" },
		{ id: "z2_links", label: "Z2 Links" },
		{ id: "z2_sats", label: "Z2 Sats" },
		{ id: "z3", label: "Z3 SAL & PIT" },
		{ id: "z4", label: "Z4 information_mart" },
		{ id: "pii", label: "PII Split" },
		{ id: "wave", label: waveChipLabel },
	];

	const graph = useMemo(
		() =>
			buildVaultCanvasGraph(vaultIr, {
				chip,
				expandedParentIds: expandedParents,
			}),
		[vaultIr, chip, expandedParents],
	);

	const setView = (v: ViewId) => {
		if (!pid) return;
		navigate(`/projects/${encodeURIComponent(pid)}/explorer?view=${v}`);
	};

	const onTabKeyDown = (e: React.KeyboardEvent, idx: number) => {
		const next =
			e.key === "ArrowRight"
				? (idx + 1) % VIEWS.length
				: e.key === "ArrowLeft"
					? (idx - 1 + VIEWS.length) % VIEWS.length
					: e.key === "Home"
						? 0
						: e.key === "End"
							? VIEWS.length - 1
							: -1;
		if (next >= 0) {
			e.preventDefault();
			const target = VIEWS[next];
			if (target) setView(target[0]);
		}
	};

	const byZone = useMemo(() => {
		const map: Record<"Z0" | "Z1" | "Z2" | "Z3" | "Z4", typeof graph.nodes> = {
			Z0: [],
			Z1: [],
			Z2: [],
			Z3: [],
			Z4: [],
		};
		for (const n of graph.nodes) {
			const z = n.data.zone ?? "Z2";
			map[z].push(n);
		}
		return map;
	}, [graph.nodes]);

	const locks = ontologyBundle?.evolution_locks ?? null;
	const waveDelta = vaultIr?.wave_delta ?? null;

	return (
		<div className="stack-md">
			<div className="row-between">
				<div>
					<h1 className="h1-serif">
						DV Schema Explorer (5-Zone Z0–Z4 &amp; information_mart)
					</h1>
					<p className="wb-sub">
						Inspect storage-neutral CertifiedVaultIR, forward-only bipartite
						lineage, target manifests, and wave locks.
					</p>
				</div>
				{vaultIr && (
					<span className="pill pill-ok">
						Wave {vaultIr.wave_number} · {graph.totalEntities} objects
					</span>
				)}
			</div>

			<div className="tab-strip" role="tablist" aria-label="Explorer Views">
				{VIEWS.map(([id, label], idx) => (
					<button
						key={id}
						type="button"
						role="tab"
						id={`exp-tab-${id}`}
						aria-selected={activeView === id}
						aria-controls={`exp-panel-${id}`}
						tabIndex={activeView === id ? 0 : -1}
						className="tab-btn"
						onClick={() => setView(id)}
						onKeyDown={(e) => onTabKeyDown(e, idx)}
					>
						{label}
					</button>
				))}
			</div>

			{(activeView === "1" || activeView === "2") && (
				<fieldset className="row" style={{ border: 0, padding: 0, margin: 0 }}>
					<legend className="visually-hidden">Zone Filter Chips</legend>
					{CHIPS.map((c) => (
						<button
							key={c.id}
							type="button"
							aria-pressed={chip === c.id}
							className={`btn btn-sm ${chip === c.id ? "btn-primary" : "btn-secondary"}`}
							onClick={() => setChip(c.id)}
						>
							{c.label}
						</button>
					))}
				</fieldset>
			)}

			<div
				role="tabpanel"
				id={`exp-panel-${activeView}`}
				aria-labelledby={`exp-tab-${activeView}`}
				className="stack-md"
			>
				{!vaultIr ? (
					<div className="callout-amber">
						No certified <code>CertifiedVaultIR</code> is available for this
						project yet. Complete Step 6 Vault Synthesis first.
					</div>
				) : (
					<>
						{activeView === "1" && (
							<div
								style={{
									display: "grid",
									gridTemplateColumns: isMobile
										? "1fr"
										: "repeat(5, minmax(0, 1fr))",
									gap: 10,
								}}
							>
								{(
									[
										["Z0", "Z0 Bronze & Refs"],
										["Z1", "Z1 Hashed Stg"],
										["Z2", "Z2 Raw Vault"],
										["Z3", "Z3 SAL & PIT/Bridge"],
										["Z4", "Z4 information_mart"],
									] as const
								).map(([zKey, title]) => (
									<section key={zKey} className="card-subtle stack-sm">
										<h2 className="font-semibold">{title}</h2>
										{byZone[zKey].length === 0 ? (
											<p className="text-muted text-sm">None</p>
										) : (
											byZone[zKey].map((n) => (
												<div
													key={n.id}
													className={
														n.data.kind === "sat_pii"
															? "callout-coral stack-sm"
															: n.data.kind === "sal" || n.data.kind === "pit"
																? "callout-teal stack-sm"
																: "card-cream stack-sm"
													}
												>
													<div className="mono text-sm font-semibold">
														{n.data.label}
													</div>
													{n.data.badge && (
														<span className="pill">{n.data.badge}</span>
													)}
												</div>
											))
										)}
									</section>
								))}
							</div>
						)}

						{activeView === "2" &&
							(isMobile ? (
								<div className="card-subtle stack-sm">
									<div className="pill pill-warn">
										View-Only Mobile Zone Topology (&lt; 768px)
									</div>
									{graph.nodes.map((n) => (
										<div key={n.id} className="card-cream row-between">
											<span className="mono text-sm">{n.data.label}</span>
											<span className="pill">{n.data.zone ?? "Z2"}</span>
										</div>
									))}
								</div>
							) : (
								<Suspense
									fallback={
										<div className="card-subtle">
											Loading Bipartite Lineage Graph...
										</div>
									}
								>
									<BipartiteGraphCanvas
										nodes={graph.nodes}
										edges={graph.edges}
										onSelectNode={(_, data) => {
											if (data.parentHubId) {
												const parentId = data.parentHubId;
												setExpandedParents((prev) => {
													const next = new Set(prev);
													next.add(parentId);
													return next;
												});
											}
										}}
										ariaLabel="5-Zone Bipartite Lineage Graph"
									/>
								</Suspense>
							))}

						{activeView === "3" && (
							<div className="split-grid">
								<section className="card-subtle stack-sm">
									<h2 className="h2-serif">
										Read-Only CertifiedVaultIR (v{vaultIr.ir_version})
									</h2>
									<pre
										className="card-dark mono text-sm"
										style={{ maxHeight: 420, overflow: "auto", margin: 0 }}
									>
										{JSON.stringify(vaultIr, null, 2)}
									</pre>
								</section>
								<section className="card-subtle stack-sm">
									<h2 className="h2-serif">
										Compiled Target Manifests ({manifests.length})
									</h2>
									{manifests.length === 0 ? (
										<p className="text-muted text-sm">
											No targets compiled yet (run Step 7 compile).
										</p>
									) : (
										manifests.map((m) => (
											<div key={m.target_id} className="card-cream stack-sm">
												<div className="row-between">
													<strong className="mono">{m.target_id}</strong>
													<span className="pill pill-ok">
														{m.certification_status} · W{m.last_compiled_wave}
													</span>
												</div>
												<div className="text-sm">
													Receipts: {(m.eval_receipts ?? []).join(", ") || "—"}
												</div>
												<div className="mono text-sm text-muted">
													Files: {(m.generated_files ?? []).join(", ")}
												</div>
											</div>
										))
									)}
								</section>
							</div>
						)}

						{activeView === "4" && (
							<div className="split-grid">
								<section className="card-subtle stack-sm">
									<h2 className="h2-serif">
										Wave Diff (
										{waveDelta
											? `W00${waveDelta.from_wave} → W00${waveDelta.to_wave}`
											: `W00${vaultIr.wave_number}`}
										)
									</h2>
									{waveDelta ? (
										<div className="stack-sm text-sm">
											<div>
												<strong>Added Entities:</strong>{" "}
												<span className="mono">
													{(waveDelta.added_entities ?? []).join(", ") ||
														"None"}
												</span>
											</div>
											<div>
												<strong>Added Extension Satellites:</strong>{" "}
												<span className="mono">
													{(waveDelta.added_sat_extensions ?? []).join(", ") ||
														"None"}
												</span>
											</div>
											<div>
												<strong>Locked Entities:</strong>{" "}
												<span className="mono">
													{(waveDelta.locked_entities ?? []).join(", ") ||
														"None"}
												</span>
											</div>
										</div>
									) : (
										<p className="text-sm text-muted">
											Initial Wave {vaultIr.wave_number} baseline.
										</p>
									)}
								</section>
								<section className="card-subtle stack-sm">
									<h2 className="h2-serif">
										Evolution Locks (
										{locks
											? `LOCK-W${String(locks.wave_number).padStart(3, "0")}`
											: "None"}
										)
									</h2>
									{locks ? (
										<pre
											className="card-dark mono text-sm"
											style={{ maxHeight: 340, overflow: "auto", margin: 0 }}
										>
											{JSON.stringify(locks, null, 2)}
										</pre>
									) : (
										<p className="text-sm text-muted">
											No evolution locks recorded yet.
										</p>
									)}
								</section>
							</div>
						)}
					</>
				)}
			</div>
		</div>
	);
}
