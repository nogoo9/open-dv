import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { LineCounter, isMap, parseDocument } from "yaml";
import { api, parseProblemDetails } from "../api/client";
import type {
	CanonicalBusinessConcept,
	GraphAOntology,
	OntologyBundleResp,
	OntologyDelta,
	OntologyEnumeration,
} from "../api/openapi";
import { useProjectStore } from "../store/projectStore";

export const pendingStagedCandidateIds = (
	bundle: OntologyBundleResp | null | undefined,
): string[] =>
	(bundle?.staged_candidates ?? [])
		.filter((c) => c.status !== "PROMOTED")
		.map((c) => c.candidate_id);

export const hasPendingStagedCandidates = (
	bundle: OntologyBundleResp | null | undefined,
): boolean => pendingStagedCandidateIds(bundle).length > 0;

const NON_CONCEPT_OP_NAMES = new Set([
	"AddRelationship",
	"DeleteRelationship",
	"AddAttribute",
	"UpdateAttributeMetadata",
	"RemoveAttribute",
	"AddEnumeration",
	"DeleteEnumeration",
	"AddEnumerationCode",
	"RemoveEnumerationCode",
	"UpdateConceptDescription",
	"AddSynonym",
	"AddTableBinding",
	"AddSourceCitation",
	"AddExternalMapping",
	"AddKeyAuthorityPattern",
	"AddMcpEvidence",
]);

export function buildEffectiveOps(
	bundle: OntologyBundleResp | null | undefined,
	baseWorking: GraphAOntology | null | undefined,
	pendingOps: readonly OntologyDelta[],
): OntologyDelta[] {
	if (!hasPendingStagedCandidates(bundle)) return [...pendingOps];
	const committed = new Set(
		(bundle?.ontology.concepts ?? []).map((c) => c.concept_uri),
	);
	const stagedOps: OntologyDelta[] = (baseWorking?.concepts ?? [])
		.filter(
			(c) =>
				!committed.has(c.concept_uri) &&
				!(
					NON_CONCEPT_OP_NAMES.has(c.concept_name) &&
					(c.attributes ?? []).length === 0 &&
					(c.table_bindings ?? []).length === 0
				),
		)
		.map((c) => ({
			op_type: "AddConcept" as const,
			concept_uri: c.concept_uri,
			concept_name: c.concept_name,
			description: c.description.startsWith("Staged (")
				? c.concept_name
				: c.description,
			parent_concept_uri: c.parent_concept_uri ?? null,
			key_authority_tier: c.key_authority_tier,
			key_authority_patterns: c.key_authority_patterns ?? [],
			bkcc_default: c.bkcc_default ?? null,
			governance_tags: (c.governance_tags ?? []).filter(
				(t) => t !== "staged:quarantined",
			),
			governance_constraints: c.governance_constraints ?? {},
			attributes: c.attributes ?? [],
			synonyms: c.synonyms ?? [],
			external_mappings: c.external_mappings ?? [],
			table_bindings: c.table_bindings ?? [],
			source_citations: c.source_citations ?? [],
			mcp_evidence: c.mcp_evidence ?? [],
			rationale: "Promote staged discovery candidate",
		}));
	return [...stagedOps, ...pendingOps];
}

export async function fetchEffectiveOntology(
	projectId: string,
	bundle: OntologyBundleResp,
	signal?: AbortSignal,
): Promise<{ yamlText: string; workingOntology: GraphAOntology }> {
	const hasStaged = hasPendingStagedCandidates(bundle);
	const [yamlText, workingOntology] = await Promise.all([
		api.exportOntology(projectId, "yaml", hasStaged, signal),
		hasStaged
			? api
					.exportOntology(projectId, "json", true, signal)
					.then((j) => JSON.parse(j) as GraphAOntology)
			: Promise.resolve(bundle.ontology),
	]);
	return { yamlText, workingOntology };
}

export function applyOntologyPreviewOp(
	ont: GraphAOntology,
	op: OntologyDelta,
): GraphAOntology {
	const cs = ont.concepts ?? [];
	const rs = ont.relationships ?? [];
	const es = ont.enumerations ?? [];
	const mapC = (
		uri: string,
		fn: (c: CanonicalBusinessConcept) => CanonicalBusinessConcept,
	) => ({
		...ont,
		concepts: cs.map((c) => (c.concept_uri === uri ? fn(c) : c)),
	});
	const mapE = (
		uri: string,
		fn: (e: OntologyEnumeration) => OntologyEnumeration,
	) => ({
		...ont,
		enumerations: es.map((e) => (e.enum_uri === uri ? fn(e) : e)),
	});
	switch (op.op_type) {
		case "AddConcept":
			return cs.some((c) => c.concept_uri === op.concept_uri)
				? ont
				: {
						...ont,
						concepts: [
							...cs,
							{
								concept_uri: op.concept_uri,
								concept_name: op.concept_name,
								description: op.description,
								key_authority_tier: op.key_authority_tier,
								bkcc_default: op.bkcc_default ?? null,
								parent_concept_uri: op.parent_concept_uri ?? null,
								attributes: (op.attributes ??
									[]) as CanonicalBusinessConcept["attributes"],
								synonyms: op.synonyms ?? [],
								governance_tags: op.governance_tags ?? [],
								governance_constraints: op.governance_constraints ?? {},
								key_authority_patterns: op.key_authority_patterns ?? [],
								external_mappings: (op.external_mappings ??
									[]) as CanonicalBusinessConcept["external_mappings"],
								table_bindings: op.table_bindings ?? [],
								source_citations: op.source_citations ?? [],
								mcp_evidence: op.mcp_evidence ?? [],
							},
						],
					};
		case "DeleteConcept":
			return {
				...ont,
				concepts: cs.filter((c) => c.concept_uri !== op.concept_uri),
				relationships: rs.filter(
					(r) =>
						r.source_concept_uri !== op.concept_uri &&
						r.target_concept_uri !== op.concept_uri,
				),
			};
		case "RenameConcept":
			return mapC(op.concept_uri, (c) => ({
				...c,
				concept_name: op.new_concept_name,
			}));
		case "SetConceptKeyAuthority":
			return mapC(op.concept_uri, (c) => ({
				...c,
				key_authority_tier: op.key_authority_tier,
				bkcc_default: op.bkcc_default ?? c.bkcc_default,
			}));
		case "AddKeyAuthorityPattern":
			return mapC(op.concept_uri, (c) => ({
				...c,
				key_authority_patterns: [
					...(c.key_authority_patterns ?? []),
					op.pattern,
				],
			}));
		case "SetDescription":
		case "ReviseDescription":
			return mapC(op.concept_uri, (c) => ({
				...c,
				description: op.description,
			}));
		case "AddAttribute":
			return mapC(op.concept_uri, (c) => ({
				...c,
				attributes: [
					...(c.attributes ?? []),
					op.attribute as NonNullable<
						CanonicalBusinessConcept["attributes"]
					>[number],
				],
			}));
		case "UpdateAttributeMetadata":
			return mapC(op.concept_uri, (c) => ({
				...c,
				attributes: (c.attributes ?? []).map((a) =>
					a.name === op.attribute_name
						? {
								...a,
								...(op.is_business_key != null
									? { is_business_key: op.is_business_key }
									: {}),
								...(op.is_pii != null ? { is_pii: op.is_pii } : {}),
								...(op.nullable != null ? { nullable: op.nullable } : {}),
								...(op.sql_type_hint != null
									? { sql_type_hint: op.sql_type_hint }
									: {}),
							}
						: a,
				),
			}));
		case "RemoveAttribute":
			return mapC(op.concept_uri, (c) => ({
				...c,
				attributes: (c.attributes ?? []).filter(
					(a) => a.name !== op.attribute_name,
				),
			}));
		case "AddRelationship":
			return {
				...ont,
				relationships: [
					...rs.filter(
						(r) => r.relationship_uri !== op.relationship.relationship_uri,
					),
					op.relationship,
				],
			};
		case "DeleteRelationship":
			return {
				...ont,
				relationships: rs.filter(
					(r) => r.relationship_uri !== op.relationship_uri,
				),
			};
		case "AddEnumeration":
			return {
				...ont,
				enumerations: [
					...es.filter((e) => e.enum_uri !== op.enumeration.enum_uri),
					op.enumeration,
				],
			};
		case "DeleteEnumeration":
			return {
				...ont,
				enumerations: es.filter((e) => e.enum_uri !== op.enum_uri),
			};
		case "AddEnumerationCode":
			return mapE(op.enum_uri, (e) => ({
				...e,
				codes: { ...e.codes, [op.code]: op.description },
			}));
		case "RemoveEnumerationCode":
			return mapE(op.enum_uri, (e) => {
				const codes = { ...e.codes };
				delete codes[op.code];
				return { ...e, codes };
			});
		default:
			return ont;
	}
}

export const applyOntologyPreviewOps = (
	base: GraphAOntology,
	ops: readonly OntologyDelta[],
): GraphAOntology =>
	ops.reduce((acc, op) => applyOntologyPreviewOp(acc, op), base);

export interface YamlCstCheckResult {
	valid: boolean;
	error: string | null;
	line: number | null;
	col: number | null;
	preservedYaml: string;
}

export function checkYamlCstSyntax(yamlText: string): YamlCstCheckResult {
	const lineCounter = new LineCounter();
	const doc = parseDocument(yamlText, {
		keepSourceTokens: true,
		lineCounter,
		prettyErrors: true,
	});
	if (doc.errors.length > 0) {
		const first = doc.errors[0];
		const pos = first?.linePos?.[0];
		return {
			valid: false,
			error: first?.message ?? "YAML syntax error",
			line: pos?.line ?? 1,
			col: pos?.col ?? 1,
			preservedYaml: yamlText,
		};
	}
	if (yamlText.trim() && !isMap(doc.contents)) {
		return {
			valid: false,
			error: "Ontology YAML root must be a mapping object.",
			line: 1,
			col: 1,
			preservedYaml: doc.toString(),
		};
	}
	return {
		valid: true,
		error: null,
		line: null,
		col: null,
		preservedYaml: doc.toString(),
	};
}

export async function enterConflictRebaseState(projectId: string) {
	const [proj, bundle, serverYaml] = await Promise.all([
		api.getProject(projectId),
		api.getOntologyBundle(projectId),
		api.exportOntology(projectId, "yaml", false),
	]);
	useProjectStore.getState().updateOntologyState({
		conflictHeadRevisionId: proj.active_ontology_rev_id ?? "head",
		conflictServerOntology: bundle,
		conflictServerYaml: serverYaml,
	});
}

export function ConflictRebaseBanner({
	projectId,
	onRebase,
}: {
	projectId: string;
	onRebase: (overwriteYaml: boolean) => void;
}) {
	const st = useProjectStore((s) => s.ontologyState);
	if (!st.conflictHeadRevisionId) return null;
	const isYaml = st.draftChannel === "yaml_text";
	return (
		<section
			className="callout-coral stack-md"
			aria-label="Revision Conflict Rebase"
		>
			<div className="row-between">
				<strong>
					409 REVISION_CONFLICT: Server advanced to {st.conflictHeadRevisionId}
				</strong>
				<div className="row">
					<button
						type="button"
						className="btn btn-primary btn-sm"
						onClick={() => onRebase(false)}
					>
						{isYaml
							? "Confirm YAML Rebase & Re-validate"
							: "Promote Server Head & Keep Queued Ops"}
					</button>
					{isYaml && (
						<button
							type="button"
							className="btn btn-danger btn-sm"
							onClick={() => onRebase(true)}
						>
							Overwrite Local YAML with Server Head
						</button>
					)}
				</div>
			</div>
			{isYaml && (
				<div className="split-grid">
					<div className="stack-sm">
						<div className="field-label">
							Server Head YAML (Read-Only · {st.conflictHeadRevisionId})
						</div>
						<pre
							className="card-dark mono text-sm"
							style={{ maxHeight: 380, overflow: "auto", margin: 0 }}
						>
							{st.conflictServerYaml ?? ""}
						</pre>
					</div>
					<div className="stack-sm">
						<div className="field-label">Local Merged YAML</div>
						<YamlCstEditor projectId={projectId} showHeader={false} />
					</div>
				</div>
			)}
		</section>
	);
}

export interface YamlCstEditorProps {
	projectId: string;
	readOnly?: boolean;
	showHeader?: boolean;
	onReviewInMode4?: () => void;
}

export function YamlCstEditor({
	projectId,
	readOnly = false,
	showHeader = true,
	onReviewInMode4,
}: YamlCstEditorProps) {
	const ontologyState = useProjectStore((s) => s.ontologyState);
	const activeProject = useProjectStore((s) => s.activeProject);
	const updateOntologyState = useProjectStore((s) => s.updateOntologyState);
	const setLastProblem = useProjectStore((s) => s.setLastProblem);

	const [validating, setValidating] = useState(false);
	const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
	const acRef = useRef<AbortController | null>(null);
	const gutterRef = useRef<HTMLDivElement>(null);
	const taRef = useRef<HTMLTextAreaElement>(null);

	const lockedByOps = ontologyState.pendingOps.length > 0;
	const isReadOnly = readOnly || lockedByOps;
	const cst = useMemo(
		() => checkYamlCstSyntax(ontologyState.yamlText),
		[ontologyState.yamlText],
	);

	const cancelPending = useCallback(() => {
		if (timerRef.current !== null) {
			clearTimeout(timerRef.current);
			timerRef.current = null;
		}
		acRef.current?.abort();
		acRef.current = null;
	}, []);

	useEffect(() => {
		const el = taRef.current;
		if (!el) return;
		const onNativeKeyDown = (e: KeyboardEvent) =>
			e.key === "Escape" && e.stopPropagation();
		el.addEventListener("keydown", onNativeKeyDown);
		return () => el.removeEventListener("keydown", onNativeKeyDown);
	}, []);

	useEffect(() => () => cancelPending(), [cancelPending]);

	const runServerValidate = async (textToValidate: string) => {
		cancelPending();
		if (!checkYamlCstSyntax(textToValidate).valid) return;
		const ac = new AbortController();
		acRef.current = ac;
		setValidating(true);
		const baseRev =
			ontologyState.base_revision_id ??
			activeProject?.active_ontology_rev_id ??
			null;
		try {
			const resp = await api.validateOntology(
				projectId,
				{ yaml_text: textToValidate, base_revision_id: baseRev },
				ac.signal,
			);
			if (!ac.signal.aborted) updateOntologyState({ validationResp: resp });
		} catch (err) {
			if (ac.signal.aborted) return;
			const p = parseProblemDetails(err);
			if (p.status === 409 || p.error_code === "REVISION_CONFLICT") {
				await enterConflictRebaseState(projectId).catch(() =>
					setLastProblem(p),
				);
			} else {
				setLastProblem(p);
			}
		} finally {
			if (!ac.signal.aborted) setValidating(false);
		}
	};

	const onTextChange = (next: string) => {
		if (isReadOnly) return;
		const isDirty = next !== ontologyState.baseYamlText;
		updateOntologyState({
			yamlText: next,
			draftChannel: isDirty ? "yaml_text" : "clean",
			dirty: isDirty,
			validationResp: null,
		});
		cancelPending();
		if (isDirty && checkYamlCstSyntax(next).valid) {
			timerRef.current = setTimeout(() => void runServerValidate(next), 800);
		}
	};

	const lineNums = useMemo(
		() =>
			Array.from(
				{ length: Math.max(1, ontologyState.yamlText.split("\n").length) },
				(_, k) => k + 1,
			),
		[ontologyState.yamlText],
	);

	const valResp = ontologyState.validationResp;
	const bump = valResp?.proposed_bump;
	const [pillClass, pillText] = !cst.valid
		? ["pill pill-err", `Invalid Line ${cst.line ?? 1}, Col ${cst.col ?? 1}`]
		: valResp && !valResp.valid
			? ["pill pill-err", `Invalid (${valResp.diagnostics.length} issues)`]
			: ontologyState.draftChannel === "yaml_text"
				? [
						"pill pill-warn",
						validating
							? "Validating..."
							: bump
								? `Unsaved Valid · ${bump.bump_kind} v${bump.current_version} -> v${bump.next_version}`
								: "Unsaved Valid",
					]
				: ["pill pill-ok", "Saved"];

	return (
		<div className="stack-md">
			{showHeader && (
				<div className="row-between">
					<div className="row">
						<span className={pillClass} data-testid="yaml-validity-pill">
							{pillText}
						</span>
						<span className="pill mono">
							Base:{" "}
							{ontologyState.base_revision_id ??
								activeProject?.active_ontology_rev_id ??
								"Genesis v0.0.0"}
						</span>
					</div>
					<div className="row">
						<button
							type="button"
							className="btn btn-sm"
							disabled={isReadOnly || !cst.valid || validating}
							onClick={() => void runServerValidate(ontologyState.yamlText)}
						>
							Validate YAML
						</button>
						{onReviewInMode4 && (
							<button
								type="button"
								className="btn btn-primary btn-sm"
								onClick={() => {
									cancelPending();
									onReviewInMode4();
								}}
							>
								Review in Mode 4 →
							</button>
						)}
					</div>
				</div>
			)}

			{lockedByOps && (
				<output className="callout-amber">
					<strong>Mode 3 Read-Only:</strong> {ontologyState.pendingOps.length}{" "}
					structured operation(s) are staged in Modes 1–2. Review and commit or
					discard them before editing raw YAML.
				</output>
			)}

			{!cst.valid && cst.error && (
				<div className="banner banner-danger" role="alert">
					YAML Syntax Error (Line {cst.line}, Col {cst.col}): {cst.error}
				</div>
			)}

			<div
				className="card-dark"
				style={{
					display: "grid",
					gridTemplateColumns: "46px minmax(0, 1fr)",
					padding: 0,
					overflow: "hidden",
				}}
			>
				<div
					ref={gutterRef}
					aria-hidden="true"
					className="mono text-sm"
					style={{
						padding: "12px 8px",
						textAlign: "right",
						color: "#a09d96",
						background: "#1f1e1b",
						borderRight: "1px solid #33312d",
						userSelect: "none",
						overflow: "hidden",
						lineHeight: "1.5",
						height: 380,
					}}
				>
					{lineNums.map((ln) => (
						<div key={ln}>{ln}</div>
					))}
				</div>
				<textarea
					ref={taRef}
					aria-label="Graph A Ontology YAML Editor"
					className="mono"
					readOnly={isReadOnly}
					spellCheck={false}
					value={ontologyState.yamlText}
					onScroll={(e) => {
						if (gutterRef.current)
							gutterRef.current.scrollTop = e.currentTarget.scrollTop;
					}}
					onChange={(e) => onTextChange(e.target.value)}
					onBlur={() => {
						if (
							!isReadOnly &&
							ontologyState.draftChannel === "yaml_text" &&
							cst.valid &&
							!ontologyState.validationResp
						) {
							void runServerValidate(ontologyState.yamlText);
						}
					}}
					onKeyDown={(e) => {
						if (e.key === "Escape") {
							e.stopPropagation();
							return;
						}
						if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "s") {
							e.preventDefault();
							if (!isReadOnly) void runServerValidate(ontologyState.yamlText);
						}
					}}
					style={{
						width: "100%",
						height: 380,
						padding: "12px",
						border: 0,
						background: "#181715",
						color: "#faf9f5",
						fontSize: 12.5,
						lineHeight: "1.5",
						resize: "vertical",
					}}
				/>
			</div>
		</div>
	);
}
