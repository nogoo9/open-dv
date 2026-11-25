import { useCallback, useEffect, useMemo, useState } from "react";
import { api, parseProblemDetails } from "../api/client";
import type {
	EvidenceGroundingApproval,
	ExternalMcpReceipt,
	OntologyCommitReq,
	OntologyWaiverSpec,
} from "../api/openapi";
import { useProjectStore } from "../store/projectStore";
import {
	ConflictRebaseBanner,
	applyOntologyPreviewOps,
	buildEffectiveOps,
	checkYamlCstSyntax,
	enterConflictRebaseState,
	fetchEffectiveOntology,
	hasPendingStagedCandidates,
	pendingStagedCandidateIds,
} from "./YamlCstEditor";

const EMPTY_W = {
	rationale: "",
	waiveQ: false,
	ap: {} as Record<string, { checked: boolean; rationale: string }>,
	lowConf: false,
	groundRat: "",
	lowConcepts: [] as string[],
	ev: [] as EvidenceGroundingApproval[],
	evUri: "",
	evRef: "",
	evRat: "",
};

export function Step5DiffAntiPattern({
	projectId,
	onCommitted,
}: { projectId: string; onCommitted?: () => void }) {
	const ont = useProjectStore((s) => s.ontologyState);
	const bundle = useProjectStore((s) => s.ontologyBundle);
	const proj = useProjectStore((s) => s.activeProject);
	const docMaps = useProjectStore((s) => s.documentMaps);
	const setOnt = useProjectStore((s) => s.updateOntologyState);
	const setBundle = useProjectStore((s) => s.setOntologyBundle);
	const setProj = useProjectStore((s) => s.setActiveProject);
	const setDirty = useProjectStore((s) => s.setDirtyFlag);
	const markSuperseded = useProjectStore((s) => s.markRunsSupersededOnCommit);
	const announce = useProjectStore((s) => s.announce);
	const setErr = useProjectStore((s) => s.setLastProblem);

	const [validating, setValidating] = useState(false);
	const [committing, setCommitting] = useState(false);
	const [receipts, setReceipts] = useState<ExternalMcpReceipt[]>([]);
	const [commitMsg, setCommitMsg] = useState<string | null>(null);
	const [w, setW] = useState(EMPTY_W);

	const baseRev = ont.base_revision_id ?? proj?.active_ontology_rev_id ?? "";
	const hasStaged = hasPendingStagedCandidates(bundle);

	useEffect(() => {
		const anyAp = Object.values(w.ap).some(
			(x) => x.checked || x.rationale.trim().length > 0,
		);
		setDirty(
			"step5Waivers",
			w.rationale.trim().length > 0 ||
				w.waiveQ ||
				anyAp ||
				w.lowConf ||
				w.groundRat.trim().length > 0 ||
				w.ev.length > 0,
		);
	}, [w, setDirty]);

	const runValidate = useCallback(async () => {
		const st = useProjectStore.getState().ontologyState;
		if (
			st.draftChannel === "yaml_text" &&
			!checkYamlCstSyntax(st.yamlText).valid
		)
			return;
		setValidating(true);
		const curBundle = useProjectStore.getState().ontologyBundle;
		const req =
			st.draftChannel === "yaml_text"
				? {
						yaml_text: st.yamlText,
						base_revision_id: st.base_revision_id ?? (baseRev || null),
					}
				: {
						operations: buildEffectiveOps(
							curBundle,
							st.baseWorkingOntology,
							st.pendingOps,
						),
						base_revision_id: st.base_revision_id ?? (baseRev || null),
					};
		try {
			setOnt({ validationResp: await api.validateOntology(projectId, req) });
		} catch (err) {
			const p = parseProblemDetails(err);
			if (p.status === 409 || p.error_code === "REVISION_CONFLICT") {
				await enterConflictRebaseState(projectId).catch(() => setErr(p));
			} else setErr(p);
		} finally {
			setValidating(false);
		}
	}, [projectId, baseRev, setErr, setOnt]);

	useEffect(() => {
		void runValidate();
	}, [runValidate]);

	useEffect(() => {
		if (!projectId) return;
		const ac = new AbortController();
		void api
			.listReceipts(projectId, ac.signal)
			.then((r) => {
				if (!ac.signal.aborted && Array.isArray(r)) setReceipts(r);
			})
			.catch(() => undefined);
		return () => ac.abort();
	}, [projectId]);

	const valResp = ont.validationResp;
	const diagnostics = valResp?.diagnostics ?? [];
	const apRows = useMemo(
		() =>
			diagnostics.filter((d) => d.waivable && d.rule_id.startsWith("ONT-AP-")),
		[diagnostics],
	);
	const hasBlocker = diagnostics.some(
		(d) =>
			(d.severity === "BLOCKER" && !d.waivable) ||
			d.rule_id.startsWith("ONT_EVO"),
	);
	const cstInvalid =
		ont.draftChannel === "yaml_text" && !checkYamlCstSyntax(ont.yamlText).valid;

	const apWaivers: OntologyWaiverSpec[] = [];
	let badAp = false;
	for (const d of apRows) {
		const draft = w.ap[`${d.rule_id}::${d.concept_uri ?? d.entity_name}`];
		if (draft?.checked) {
			if (draft.rationale.trim().length < 20) badAp = true;
			apWaivers.push({
				rule_id: d.rule_id,
				concept_uri: d.concept_uri ?? d.entity_name,
				rationale: draft.rationale.trim(),
				waiver_id: "",
				created_at: "",
			});
		}
	}

	const commitDisabled =
		committing ||
		cstInvalid ||
		validating ||
		!valResp ||
		ont.conflictHeadRevisionId !== null ||
		hasBlocker ||
		w.rationale.trim().length === 0 ||
		(w.waiveQ && w.rationale.trim().length < 20) ||
		badAp ||
		(w.lowConf && w.groundRat.trim().length < 20);

	const submitCommit = async () => {
		if (commitDisabled) return;
		setCommitting(true);
		setCommitMsg(null);
		const shared = {
			base_revision_id: baseRev,
			rationale: w.rationale.trim(),
			waive_quarantine: w.waiveQ,
			ont_ap_waivers: apWaivers,
			committed_proposal_ids: [
				...new Set([
					...ont.committedProposalIds,
					...pendingStagedCandidateIds(bundle),
				]),
			],
			dismissed_proposal_ids: ont.dismissedProposalIds,
			low_confidence_grounding_waiver: w.lowConf,
			grounding_rationale: w.lowConf ? w.groundRat.trim() : null,
			low_confidence_waiver_concepts:
				w.lowConf && w.lowConcepts.length > 0 ? w.lowConcepts : null,
			evidence_grounding: w.ev.length > 0 ? w.ev : null,
		};
		const req: OntologyCommitReq =
			ont.draftChannel === "yaml_text"
				? { ...shared, yaml_text: ont.yamlText }
				: {
						...shared,
						operations: buildEffectiveOps(
							bundle,
							ont.baseWorkingOntology,
							ont.pendingOps,
						),
					};
		try {
			const rev = await api.commitOntology(projectId, req);
			const [nextProj, nextBundle] = await Promise.all([
				api.getProject(projectId),
				api.getOntologyBundle(projectId),
			]);
			const { yamlText, workingOntology } = await fetchEffectiveOntology(
				projectId,
				nextBundle,
			);
			setProj(nextProj);
			setBundle(nextBundle);
			markSuperseded(rev.revision_id);
			setOnt({
				draftChannel: "clean",
				base_revision_id: rev.revision_id,
				baseWorkingOntology: workingOntology,
				workingOntology,
				baseYamlText: yamlText,
				yamlText,
				pendingOps: [],
				committedProposalIds: [],
				dismissedProposalIds: [],
				dirty: false,
				conflictHeadRevisionId: null,
				conflictServerOntology: null,
				conflictServerYaml: null,
				validationResp: null,
			});
			setW(EMPTY_W);
			setCommitMsg(`Committed ${rev.revision_id} (v${rev.ontology_version})`);
			announce("polite", `Ontology committed ${rev.revision_id}`);
			onCommitted?.();
		} catch (err) {
			const p = parseProblemDetails(err);
			if (p.status === 409 || p.error_code === "REVISION_CONFLICT") {
				await enterConflictRebaseState(projectId).catch(() => setErr(p));
			} else setErr(p); // Never auto-check waive_quarantine on 422
		} finally {
			setCommitting(false);
		}
	};

	const confirmRebase = async (overwriteYaml = false) => {
		const {
			conflictHeadRevisionId: headRev,
			conflictServerOntology: headBundle,
			conflictServerYaml,
		} = ont;
		if (!headRev || !headBundle) return;
		const headYaml = conflictServerYaml ?? "";
		const { workingOntology: baseOnt } = hasPendingStagedCandidates(headBundle)
			? await fetchEffectiveOntology(projectId, headBundle)
			: { workingOntology: headBundle.ontology };
		setBundle(headBundle);
		const keepYaml = ont.draftChannel === "yaml_text" && !overwriteYaml;
		setOnt({
			base_revision_id: headRev,
			baseWorkingOntology: baseOnt,
			workingOntology: keepYaml
				? baseOnt
				: applyOntologyPreviewOps(baseOnt, overwriteYaml ? [] : ont.pendingOps),
			baseYamlText: headYaml,
			...(keepYaml
				? {}
				: {
						draftChannel: overwriteYaml ? ("clean" as const) : ont.draftChannel,
						dirty: overwriteYaml ? false : ont.dirty,
						pendingOps: overwriteYaml ? [] : ont.pendingOps,
						yamlText: headYaml,
					}),
			conflictHeadRevisionId: null,
			conflictServerOntology: null,
			conflictServerYaml: null,
			validationResp: null,
		});
		await runValidate();
	};

	const downloadExport = async (fmt: "yaml" | "json" | "ttl") => {
		try {
			const url = URL.createObjectURL(
				new Blob([await api.exportOntology(projectId, fmt, hasStaged)], {
					type: "text/plain;charset=utf-8",
				}),
			);
			const a = document.createElement("a");
			a.href = url;
			a.download = `${projectId}-ontology.${fmt}`;
			a.click();
			URL.revokeObjectURL(url);
		} catch (err) {
			setErr(parseProblemDetails(err));
		}
	};

	const concepts = (ont.workingOntology ?? bundle?.ontology)?.concepts ?? [];
	const refs = useMemo(
		() => [
			...new Set([
				...concepts.flatMap((c) => [
					...(c.table_bindings ?? []).map(
						(tb) => `${tb.source_id}:${tb.table_id}.${tb.col_name}`,
					),
					...(c.source_citations ?? []).map(
						(cit) => `${cit.doc_id}#p${cit.page_number}`,
					),
				]),
				...docMaps.map((d) => `${d.doc_id}#p1`),
				...receipts.map((r) => `mcp:${r.receipt_id}`),
			]),
		],
		[concepts, docMaps, receipts],
	);

	return (
		<div className="stack-md">
			{commitMsg && <output className="banner banner-ok">{commitMsg}</output>}
			<ConflictRebaseBanner
				projectId={projectId}
				onRebase={(ow) => void confirmRebase(ow)}
			/>

			<section className="card-subtle row-between">
				<div className="stack-sm">
					<h2 className="h2-serif">Server Validation &amp; Staged Summary</h2>
					<div className="row">
						<span className="pill">
							Channel: <strong>{ont.draftChannel}</strong>
						</span>
						<span className="pill">Ops: {ont.pendingOps.length}</span>
						<span className="pill">
							Committed Proposals: {ont.committedProposalIds.length}
						</span>
						<span className="pill">
							Dismissed Proposals: {ont.dismissedProposalIds.length}
						</span>
						{valResp && (
							<span
								className={`pill ${valResp.valid ? "pill-ok" : "pill-warn"}`}
							>
								{valResp.proposed_bump.bump_kind} v
								{valResp.proposed_bump.current_version} → v
								{valResp.proposed_bump.next_version}
							</span>
						)}
					</div>
				</div>
				<button
					type="button"
					className="btn btn-sm"
					disabled={validating || cstInvalid}
					onClick={() => void runValidate()}
				>
					{validating ? "Validating..." : "Re-validate"}
				</button>
			</section>

			<section className="card-subtle stack-sm">
				<h3 className="font-semibold">
					Diagnostics &amp; Anti-Pattern Waivers (ONT-AP-001..005)
				</h3>
				{diagnostics.length === 0 ? (
					<p className="text-muted text-sm">
						Zero validation warnings or anti-pattern blockers.
					</p>
				) : (
					diagnostics.map((d) => {
						const wKey = `${d.rule_id}::${d.concept_uri ?? d.entity_name}`;
						const isAp = d.waivable && d.rule_id.startsWith("ONT-AP-");
						const draft = w.ap[wKey] ?? { checked: false, rationale: "" };
						return (
							<div
								key={`${wKey}::${d.message}`}
								className={
									d.severity === "BLOCKER" && !d.waivable
										? "banner banner-danger stack-sm"
										: "card-cream stack-sm"
								}
							>
								<div className="row-between">
									<span>
										<strong>{d.rule_id}</strong> ({d.severity}) ·{" "}
										<span className="mono">{d.entity_name}</span>
									</span>
									{!d.waivable && (
										<span className="pill pill-err">Non-waivable Blocker</span>
									)}
								</div>
								<p className="text-sm">{d.message}</p>
								{isAp && (
									<div className="stack-sm">
										<label className="radio-row">
											<input
												type="checkbox"
												checked={draft.checked}
												onChange={(e) =>
													setW((p) => ({
														...p,
														ap: {
															...p.ap,
															[wKey]: { ...draft, checked: e.target.checked },
														},
													}))
												}
											/>
											<span>
												Sign ONT-AP Waiver (server assigns <code>ONTW-*</code>{" "}
												on commit)
											</span>
										</label>
										{draft.checked && (
											<input
												type="text"
												className="input"
												placeholder="Enter >= 20 character business rationale..."
												aria-label={`Waiver rationale for ${d.rule_id}`}
												value={draft.rationale}
												onChange={(e) =>
													setW((p) => ({
														...p,
														ap: {
															...p.ap,
															[wKey]: { ...draft, rationale: e.target.value },
														},
													}))
												}
											/>
										)}
									</div>
								)}
							</div>
						);
					})
				)}
			</section>

			<section className="split-grid">
				<div className="card-subtle stack-sm">
					<h3 className="font-semibold">
						Grounding Waiver &amp; Evidence Grounding Approvals
					</h3>
					<label className="radio-row">
						<input
							type="checkbox"
							checked={w.lowConf}
							onChange={(e) =>
								setW((p) => ({ ...p, lowConf: e.target.checked }))
							}
						/>
						<span>Low-Confidence Grounding Waiver</span>
					</label>
					{w.lowConf && (
						<div className="stack-sm">
							<input
								type="text"
								className="input"
								placeholder="Grounding rationale (>= 20 chars)..."
								value={w.groundRat}
								onChange={(e) =>
									setW((p) => ({ ...p, groundRat: e.target.value }))
								}
							/>
							<select
								multiple
								className="select"
								aria-label="Waived concept URIs"
								value={w.lowConcepts}
								onChange={(e) =>
									setW((p) => ({
										...p,
										lowConcepts: Array.from(
											e.target.selectedOptions,
											(o) => o.value,
										),
									}))
								}
							>
								{concepts.map((c) => (
									<option key={c.concept_uri} value={c.concept_uri}>
										{c.concept_name} ({c.concept_uri})
									</option>
								))}
							</select>
						</div>
					)}
					<div className="stack-sm">
						<span className="field-label">
							Attach Explicit <code>EvidenceGroundingApproval</code>
						</span>
						<select
							className="select"
							value={w.evUri || concepts[0]?.concept_uri || ""}
							onChange={(e) => setW((p) => ({ ...p, evUri: e.target.value }))}
						>
							{concepts.map((c) => (
								<option key={c.concept_uri} value={c.concept_uri}>
									{c.concept_name}
								</option>
							))}
						</select>
						<input
							type="text"
							className="input"
							list="ev-source-ref-suggestions"
							placeholder="src_id:table.col, doc_id#p1, or mcp:rcpt_..."
							value={w.evRef}
							onChange={(e) => setW((p) => ({ ...p, evRef: e.target.value }))}
						/>
						<datalist id="ev-source-ref-suggestions">
							{refs.map((ref) => (
								<option key={ref} value={ref} />
							))}
						</datalist>
						<div className="row">
							<input
								type="text"
								className="input"
								placeholder="Evidence grounding rationale..."
								value={w.evRat}
								onChange={(e) => setW((p) => ({ ...p, evRat: e.target.value }))}
							/>
							<button
								type="button"
								className="btn btn-sm"
								disabled={
									!(w.evUri || concepts[0]?.concept_uri) ||
									!w.evRef.trim() ||
									!w.evRat.trim()
								}
								onClick={() => {
									const uri = w.evUri || concepts[0]?.concept_uri || "";
									setW((p) => ({
										...p,
										ev: [
											...p.ev,
											{
												concept_uri: uri,
												source_refs: p.evRef
													.split(",")
													.map((s) => s.trim())
													.filter(Boolean),
												rationale: p.evRat.trim(),
											},
										],
										evRef: "",
										evRat: "",
									}));
								}}
							>
								Add Evidence Ref
							</button>
						</div>
						{w.ev.map((ev) => (
							<div
								key={`${ev.concept_uri}:${ev.source_refs.join(",")}:${ev.rationale}`}
								className="pill"
							>
								{ev.concept_uri} · {ev.source_refs.join(", ")}
							</div>
						))}
					</div>
				</div>

				<div className="card-cream stack-sm">
					<h3 className="font-semibold">Commit &amp; Quarantine Waiver Gate</h3>
					<label className="field-col">
						<span className="field-label">Commit Rationale</span>
						<textarea
							rows={3}
							className="textarea"
							placeholder="Describe this ontology revision (min 20 chars if waiving Path B2 quarantine)..."
							value={w.rationale}
							onChange={(e) =>
								setW((p) => ({ ...p, rationale: e.target.value }))
							}
						/>
					</label>
					<label className="radio-row">
						<input
							type="checkbox"
							checked={w.waiveQ}
							onChange={(e) =>
								setW((p) => ({ ...p, waiveQ: e.target.checked }))
							}
						/>
						<span>
							Explicitly waive Path B2 ungrounded quarantine (requires &ge; 20
							chars rationale)
						</span>
					</label>
					<div className="row-between">
						<div className="row">
							{(["yaml", "json", "ttl"] as const).map((fmt) => (
								<button
									key={fmt}
									type="button"
									className="btn btn-sm"
									onClick={() => void downloadExport(fmt)}
								>
									Export .{fmt}
								</button>
							))}
						</div>
						<button
							type="button"
							className="btn btn-primary"
							disabled={commitDisabled}
							onClick={() => void submitCommit()}
						>
							{committing
								? "Committing..."
								: `Validate & Commit v${valResp?.proposed_bump.next_version ?? "0.1.0"} →`}
						</button>
					</div>
				</div>
			</section>
		</div>
	);
}
