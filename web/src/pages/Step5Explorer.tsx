import { Suspense, lazy, useEffect, useMemo, useState } from "react";
import { api, parseProblemDetails } from "../api/client";
import type {
	FiboMatchSpec,
	OntologyDelta,
	OntologyRelationship,
} from "../api/openapi";
import { AskFiboDrawer } from "../components/AskFiboDrawer";
import {
	Step5CatalogKeyTier,
	Step5InspectorColumn,
} from "../components/Step5CatalogKeyTier";
import { Step5DiffAntiPattern } from "../components/Step5DiffAntiPattern";
import {
	YamlCstEditor,
	applyOntologyPreviewOps,
	fetchEffectiveOntology,
	hasPendingStagedCandidates,
} from "../components/YamlCstEditor";
import { buildOntologyCanvasGraph } from "../components/graphBuilders";
import { useProjectStore } from "../store/projectStore";

const BipartiteGraphCanvas = lazy(() =>
	import("../components/BipartiteGraphCanvas").then((m) => ({
		default: m.BipartiteGraphCanvas,
	})),
);

const MODES = [
	["1", "1 Canvas & Inspector"],
	["2", "2 Catalog & Keys"],
	["3", "3 YAML Editor"],
	["4", "4 Validation & Waivers"],
] as const;
const CARDS: readonly [OntologyRelationship["cardinality"], string][] = [
	["ONE_TO_ONE", "1:1"],
	["ONE_TO_MANY", "1:N"],
	["MANY_TO_ONE", "N:1"],
	["MANY_TO_MANY", "M:N"],
];

type ModeId = (typeof MODES)[number][0];

export function Step5Explorer() {
	const proj = useProjectStore((s) => s.activeProject);
	const currentUrl = useProjectStore((s) => s.currentUrl);
	const bundle = useProjectStore((s) => s.ontologyBundle);
	const ont = useProjectStore((s) => s.ontologyState);
	const hiddenIds = useProjectStore((s) => s.hiddenProposalIds);
	const sources = useProjectStore((s) => s.sources);
	const navigate = useProjectStore((s) => s.navigate);
	const setBundle = useProjectStore((s) => s.setOntologyBundle);
	const setOnt = useProjectStore((s) => s.updateOntologyState);
	const hideProposal = useProjectStore((s) => s.hideProposalInSession);
	const discard = useProjectStore((s) => s.applyDiscardReset);
	const setErr = useProjectStore((s) => s.setLastProblem);

	const parsed = new URL(currentUrl, "http://localhost");
	const rm = parsed.searchParams.get("mode") ?? "1";
	const mode: ModeId = rm === "2" || rm === "3" || rm === "4" ? rm : "1";
	const pid =
		proj?.project_id ?? /^\/projects\/([^/]+)/.exec(parsed.pathname)?.[1] ?? "";

	const [isMobile, setIsMobile] = useState(
		() => typeof window !== "undefined" && window.innerWidth < 768,
	);
	const [fiboOpen, setFiboOpen] = useState(false);
	const [selectedUri, setSelectedUri] = useState<string | null>(null);
	const [showAll, setShowAll] = useState(false);
	const [srcFilter, setSrcFilter] = useState("");
	const [cDraft, setCDraft] = useState({ name: "", desc: "" });
	const [rDraft, setRDraft] = useState<{
		src: string;
		tgt: string;
		verb: string;
		card: OntologyRelationship["cardinality"];
	}>({
		src: "",
		tgt: "",
		verb: "",
		card: "ONE_TO_MANY",
	});

	useEffect(() => {
		if (typeof window === "undefined") return;
		const onResize = () => setIsMobile(window.innerWidth < 768);
		window.addEventListener("resize", onResize);
		return () => window.removeEventListener("resize", onResize);
	}, []);

	useEffect(() => {
		if (
			!pid ||
			useProjectStore.getState().ontologyState.draftChannel !== "clean"
		)
			return;
		const ac = new AbortController();
		void (async () => {
			try {
				const b = await api.getOntologyBundle(pid, ac.signal);
				if (ac.signal.aborted) return;
				setBundle(b);
				const { yamlText, workingOntology } = await fetchEffectiveOntology(
					pid,
					b,
					ac.signal,
				);
				const st = useProjectStore.getState();
				if (ac.signal.aborted || st.ontologyState.draftChannel !== "clean")
					return;
				setOnt({
					base_revision_id: st.activeProject?.active_ontology_rev_id ?? null,
					baseWorkingOntology: workingOntology,
					workingOntology,
					baseYamlText: yamlText,
					yamlText,
					validationResp: null,
				});
			} catch (err) {
				if (!ac.signal.aborted) setErr(parseProblemDetails(err));
			}
		})();
		return () => ac.abort();
	}, [pid, setBundle, setErr, setOnt]);

	const goMode = (m: ModeId) =>
		pid && navigate(`/projects/${encodeURIComponent(pid)}/step/5?mode=${m}`);

	const onTabKeyDown = (e: React.KeyboardEvent, idx: number) => {
		const map: Record<string, number> = {
			ArrowRight: (idx + 1) % MODES.length,
			ArrowLeft: (idx - 1 + MODES.length) % MODES.length,
			Home: 0,
			End: MODES.length - 1,
		};
		const next = map[e.key];
		if (next !== undefined) {
			e.preventDefault();
			const target = MODES[next];
			if (target) goMode(target[0]);
		}
	};

	const stageDeltas = (ops: readonly OntologyDelta[], proposalId?: string) => {
		const st = useProjectStore.getState();
		if (st.ontologyState.draftChannel === "yaml_text") return;
		const base =
			st.ontologyState.baseWorkingOntology ?? st.ontologyBundle?.ontology;
		if (!base) return;
		const pendingOps = [...st.ontologyState.pendingOps, ...ops];
		const committedProposalIds =
			proposalId && !st.ontologyState.committedProposalIds.includes(proposalId)
				? [...st.ontologyState.committedProposalIds, proposalId]
				: st.ontologyState.committedProposalIds;
		setOnt({
			draftChannel: "operations",
			dirty: true,
			pendingOps,
			committedProposalIds,
			workingOntology: applyOntologyPreviewOps(base, pendingOps),
			validationResp: null,
		});
	};

	const dismissProposal = (id: string) =>
		setOnt((p) => ({
			...p,
			dismissedProposalIds: p.dismissedProposalIds.includes(id)
				? p.dismissedProposalIds
				: [...p.dismissedProposalIds, id],
		}));

	const workingOnt = ont.workingOntology ?? bundle?.ontology ?? null;
	const ns =
		workingOnt?.domain_namespace ?? proj?.domain_namespace ?? "enterprise";
	const concepts = workingOnt?.concepts ?? [];
	const selected =
		concepts.find((c) => c.concept_uri === selectedUri) ?? concepts[0] ?? null;
	const opts = concepts.map((c) => (
		<option key={c.concept_uri} value={c.concept_uri}>
			{c.concept_name}
		</option>
	));

	const proposals = (bundle?.ontology_proposals ?? []).filter(
		(p) =>
			p.status === "PENDING_STEWARD" &&
			!hiddenIds.includes(p.proposal_id) &&
			!ont.committedProposalIds.includes(p.proposal_id) &&
			!ont.dismissedProposalIds.includes(p.proposal_id),
	);

	const canvas = useMemo(
		() =>
			buildOntologyCanvasGraph(workingOnt, {
				stagedCandidates: bundle?.staged_candidates ?? [],
				evolutionLocks: bundle?.evolution_locks,
				focusConceptUri: selected?.concept_uri ?? null,
				showAll,
				sourceFilter: srcFilter || null,
			}),
		[workingOnt, bundle, selected, showAll, srcFilter],
	);

	const lockedYaml = ont.draftChannel === "yaml_text";
	const dirtyDraft =
		ont.draftChannel !== "clean" || ont.dismissedProposalIds.length > 0;

	const stageFibo = (m: FiboMatchSpec) =>
		stageDeltas([
			{
				op_type: "AddConcept",
				concept_uri: m.concept_uri,
				concept_name: m.label,
				description: m.definition,
				key_authority_tier: "TIER_2_ENTERPRISE_SOR",
				rationale: "Ask FIBO",
				external_mappings: [
					{
						standard_uri: m.concept_uri,
						mapping_type:
							m.suggested_path === "PATH_A1"
								? "skos:exactMatch"
								: "skos:closeMatch",
						confidence: m.normalized_score,
						reference_pack_id: "fibo-core",
						reference_pack_sha256: "",
					},
				],
			},
		]);

	const createConcept = (e: React.FormEvent) => {
		e.preventDefault();
		const raw = cDraft.name.trim();
		if (lockedYaml || !raw) return;
		const isUri = /^[a-z][a-z0-9+.-]*:/i.test(raw);
		const uri = isUri
			? raw
			: `urn:opendv:${ns}:${raw.replace(/[^A-Za-z0-9_]+/g, "_")}`;
		const name = isUri
			? (raw.split(/[:/#]/).filter(Boolean).pop() ?? raw)
			: raw;
		stageDeltas([
			{
				op_type: "AddConcept",
				concept_uri: uri,
				concept_name: name,
				description: cDraft.desc.trim() || `Business concept ${name}`,
				key_authority_tier: "TIER_2_ENTERPRISE_SOR",
				rationale: "Mode 1 AddConcept",
			},
		]);
		setSelectedUri(uri);
		setCDraft({ name: "", desc: "" });
	};

	const createRel = (e: React.FormEvent) => {
		e.preventDefault();
		const src = rDraft.src || concepts[0]?.concept_uri || "";
		const tgt = rDraft.tgt || concepts[1]?.concept_uri || src;
		const verb = rDraft.verb.trim();
		if (lockedYaml || !src || !tgt || !verb) return;
		const slug = verb.toLowerCase().replace(/[^a-z0-9_]+/g, "_");
		stageDeltas([
			{
				op_type: "AddRelationship",
				rationale: "Mode 1 AddRelationship",
				relationship: {
					relationship_uri: `urn:opendv:${ns}:rel:${slug}_${Date.now().toString(36)}`,
					relationship_name: verb,
					source_concept_uri: src,
					target_concept_uri: tgt,
					cardinality: rDraft.card,
					grain_semantics: `${rDraft.card} relationship`,
					description: verb,
					target_link: `lnk_${slug}`,
				},
			},
		]);
		setRDraft((p) => ({ ...p, verb: "" }));
	};

	const rev =
		ont.base_revision_id ?? proj?.active_ontology_rev_id ?? "Genesis v0.0.0";

	return (
		<div className="stack-md">
			<div className="row-between">
				<div>
					<h1 className="h1-serif">Step 5 · Ontology Explorer (Graph A)</h1>
					<p className="wb-sub">
						Inspect Graph A concepts, resolve Step 6 proposals, edit keys &amp;
						YAML, and commit OCC revisions ({rev}
						{hasPendingStagedCandidates(bundle) ? " · includes staged" : ""}).
					</p>
				</div>
				<div className="row">
					<button
						type="button"
						className="btn btn-sm"
						onClick={() => setFiboOpen(true)}
					>
						Ask FIBO
					</button>
					{dirtyDraft && (
						<button
							type="button"
							className="btn btn-danger btn-sm"
							onClick={() => discard()}
						>
							Discard Draft
						</button>
					)}
				</div>
			</div>

			<div
				className="tab-strip"
				role="tablist"
				aria-label="Ontology Explorer Modes"
			>
				{MODES.map(([id, label], idx) => (
					<button
						key={id}
						type="button"
						role="tab"
						id={`step5-tab-${id}`}
						aria-selected={mode === id}
						aria-controls={`step5-panel-${id}`}
						tabIndex={mode === id ? 0 : -1}
						className="tab-btn"
						onClick={() => goMode(id)}
						onKeyDown={(e) => onTabKeyDown(e, idx)}
					>
						{label}
					</button>
				))}
			</div>

			<div
				role="tabpanel"
				id={`step5-panel-${mode}`}
				aria-labelledby={`step5-tab-${mode}`}
				className="stack-md"
			>
				{mode === "1" && (
					<div
						className="split-grid"
						style={{ gridTemplateColumns: "1.45fr 1fr" }}
					>
						<div className="stack-md">
							{lockedYaml && (
								<output className="callout-amber">
									YAML edits unsaved in Mode 3. Commit or discard YAML before
									authoring in Mode 1.
								</output>
							)}
							<div className="row-between">
								<div className="row">
									<label className="row">
										<span className="text-sm text-muted">Source Filter:</span>
										<select
											className="select"
											style={{ width: "auto", maxWidth: "min(100%, 260px)" }}
											value={srcFilter}
											onChange={(e) => setSrcFilter(e.target.value)}
										>
											<option value="">All Sources</option>
											{sources.map((s) => (
												<option key={s.source_id} value={s.source_id}>
													{s.uri_or_filename}
												</option>
											))}
										</select>
									</label>
									{canvas.totalEntities > 40 && (
										<button
											type="button"
											className="btn btn-sm"
											onClick={() => setShowAll((v) => !v)}
										>
											{showAll
												? "Focus 1-Hop Neighborhood"
												: `Show All Concepts (${canvas.totalEntities})`}
										</button>
									)}
								</div>
								<span className="text-sm text-muted">
									{concepts.length} concepts ·{" "}
									{workingOnt?.relationships?.length ?? 0} relationships
								</span>
							</div>

							{isMobile ? (
								<div className="card-subtle stack-sm">
									<div className="pill pill-warn">
										View-Only Mobile Graph Summary (&lt; 768px)
									</div>
									{concepts.map((c) => (
										<button
											key={c.concept_uri}
											type="button"
											className="card-cream stack-sm"
											style={{ textAlign: "left" }}
											onClick={() => setSelectedUri(c.concept_uri)}
										>
											<strong>{c.concept_name}</strong>
											<span className="mono text-sm text-muted">
												{c.concept_uri}
											</span>
										</button>
									))}
								</div>
							) : (
								<Suspense
									fallback={
										<div className="card-subtle">Loading Graph Canvas...</div>
									}
								>
									<BipartiteGraphCanvas
										nodes={canvas.nodes}
										edges={canvas.edges}
										selectedNodeId={
											selected ? `c:${selected.concept_uri}` : null
										}
										onSelectNode={(id) =>
											id.startsWith("c:") && setSelectedUri(id.slice(2))
										}
										ariaLabel="Graph A Business Ontology Canvas"
									/>
								</Suspense>
							)}

							{!isMobile && (
								<div className="grid-2">
									<form
										className="card-subtle stack-sm"
										onSubmit={createConcept}
									>
										<h2 className="h2-serif">Add Concept</h2>
										<input
											type="text"
											className="input"
											disabled={lockedYaml}
											placeholder="Concept name or URN/URI..."
											value={cDraft.name}
											onChange={(e) =>
												setCDraft((p) => ({ ...p, name: e.target.value }))
											}
										/>
										<input
											type="text"
											className="input"
											disabled={lockedYaml}
											placeholder="Business description..."
											value={cDraft.desc}
											onChange={(e) =>
												setCDraft((p) => ({ ...p, desc: e.target.value }))
											}
										/>
										<button
											type="submit"
											className="btn btn-primary btn-sm"
											disabled={lockedYaml || !cDraft.name.trim()}
										>
											Stage AddConcept
										</button>
									</form>

									<form className="card-subtle stack-sm" onSubmit={createRel}>
										<h2 className="h2-serif">Add Relationship</h2>
										<div className="grid-2">
											<select
												className="select"
												disabled={lockedYaml}
												value={rDraft.src || concepts[0]?.concept_uri || ""}
												onChange={(e) =>
													setRDraft((p) => ({ ...p, src: e.target.value }))
												}
											>
												{opts}
											</select>
											<select
												className="select"
												disabled={lockedYaml}
												value={
													rDraft.tgt ||
													concepts[1]?.concept_uri ||
													concepts[0]?.concept_uri ||
													""
												}
												onChange={(e) =>
													setRDraft((p) => ({ ...p, tgt: e.target.value }))
												}
											>
												{opts}
											</select>
										</div>
										<div className="grid-2">
											<input
												type="text"
												className="input"
												disabled={lockedYaml}
												placeholder="Verb (e.g. HOLDS_BALLOT)"
												value={rDraft.verb}
												onChange={(e) =>
													setRDraft((p) => ({ ...p, verb: e.target.value }))
												}
											/>
											<select
												className="select"
												disabled={lockedYaml}
												value={rDraft.card}
												onChange={(e) =>
													setRDraft((p) => ({
														...p,
														card: e.target
															.value as OntologyRelationship["cardinality"],
													}))
												}
											>
												{CARDS.map(([val, lbl]) => (
													<option key={val} value={val}>
														{lbl}
													</option>
												))}
											</select>
										</div>
										<button
											type="submit"
											className="btn btn-secondary btn-sm"
											disabled={
												lockedYaml || !rDraft.verb.trim() || !concepts.length
											}
										>
											Stage Relationship
										</button>
									</form>
								</div>
							)}
						</div>

						<Step5InspectorColumn
							selectedConcept={selected}
							evolutionLocks={bundle?.evolution_locks}
							lockedByYaml={lockedYaml}
							isMobile={isMobile}
							visibleProposals={proposals}
							onStageOps={stageDeltas}
							onDismissProposal={dismissProposal}
							onHideProposal={hideProposal}
						/>
					</div>
				)}

				{mode === "2" && (
					<Step5CatalogKeyTier onStageOp={(op) => stageDeltas([op])} />
				)}
				{mode === "3" && (
					<YamlCstEditor projectId={pid} onReviewInMode4={() => goMode("4")} />
				)}
				{mode === "4" && <Step5DiffAntiPattern projectId={pid} />}
			</div>

			<div className="card-subtle row-between" style={{ minHeight: 52 }}>
				<button
					type="button"
					className="btn btn-secondary"
					onClick={() =>
						navigate(`/projects/${encodeURIComponent(pid)}/step/4`)
					}
				>
					← Step 4 Discovery
				</button>
				<button
					type="button"
					className={mode !== "4" ? "btn btn-primary" : "btn btn-secondary"}
					onClick={() =>
						mode !== "4"
							? goMode("4")
							: navigate(`/projects/${encodeURIComponent(pid)}/step/6`)
					}
				>
					{mode !== "4"
						? "Validate & Review (Mode 4) →"
						: "Proceed to Step 6 Vault →"}
				</button>
			</div>

			<AskFiboDrawer
				open={fiboOpen}
				onClose={() => setFiboOpen(false)}
				projectId={pid || null}
				domainNamespace={ns}
				initialQuery={selected?.concept_name}
				canStageToDraft={!lockedYaml}
				onStageConcept={stageFibo}
				onSetDescription={(uri, m) =>
					stageDeltas([
						{
							op_type: "SetDescription",
							concept_uri: uri,
							description: m.definition,
							rationale: "FIBO def",
						},
					])
				}
			/>
		</div>
	);
}
