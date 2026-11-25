import { useState } from "react";
import type {
	CanonicalBusinessConcept,
	EvolutionLockSpec,
	KeyTierEnum,
	OntologyDelta,
	OntologyProposalSpec,
	PortableLogicalType,
} from "../api/openapi";
import { useProjectStore } from "../store/projectStore";
import { isConceptLockedInEvolution } from "./graphBuilders";

const KEY_TIERS: readonly [KeyTierEnum, string][] = [
	["TIER_1_GLOBAL_STANDARD", "Tier 1 · Global Standard"],
	["TIER_2_ENTERPRISE_SOR", "Tier 2 · Enterprise SOR"],
	["TIER_3_LOCAL_SCOPED", "Tier 3 · Local Scoped"],
];
const ATTR_TYPES: readonly Extract<PortableLogicalType, string>[] = [
	"UTF8_STRING",
	"INT64",
	"DATE32",
	"TIMESTAMP_UTC_US",
	"BOOLEAN",
];

export function Step5InspectorColumn({
	selectedConcept,
	evolutionLocks,
	lockedByYaml,
	isMobile,
	visibleProposals,
	onStageOps,
	onDismissProposal,
	onHideProposal,
}: {
	selectedConcept: CanonicalBusinessConcept | null;
	evolutionLocks: EvolutionLockSpec | null | undefined;
	lockedByYaml: boolean;
	isMobile: boolean;
	visibleProposals: OntologyProposalSpec[];
	onStageOps: (ops: readonly OntologyDelta[], proposalId?: string) => void;
	onDismissProposal: (proposalId: string) => void;
	onHideProposal: (proposalId: string) => void;
}) {
	const [attr, setAttr] = useState<{
		name: string;
		type: Extract<PortableLogicalType, string>;
		hint: string;
		bk: boolean;
		pii: boolean;
	}>({
		name: "",
		type: "UTF8_STRING",
		hint: "VARCHAR",
		bk: false,
		pii: false,
	});

	return (
		<div className="stack-md">
			<section className="card-subtle stack-sm">
				<h2 className="h2-serif">Concept &amp; Graph B Evidence</h2>
				{!selectedConcept ? (
					<p className="text-muted text-sm">
						Select a concept node to inspect attributes and evidence.
					</p>
				) : (
					<div className="stack-sm">
						<div className="row-between">
							<strong>{selectedConcept.concept_name}</strong>
							{isConceptLockedInEvolution(selectedConcept, evolutionLocks) ? (
								<span className="pill pill-dark">Vault Locked</span>
							) : (
								<button
									type="button"
									className="btn btn-danger btn-sm"
									disabled={lockedByYaml}
									onClick={() =>
										onStageOps([
											{
												op_type: "DeleteConcept",
												concept_uri: selectedConcept.concept_uri,
												rationale: "Mode 1 delete",
											},
										])
									}
								>
									Delete Concept
								</button>
							)}
						</div>
						<div className="mono text-sm text-muted">
							{selectedConcept.concept_uri}
						</div>
						<p className="text-sm">{selectedConcept.description}</p>
						<div className="text-sm">
							<strong>Graph B Evidence:</strong>{" "}
							{selectedConcept.table_bindings?.length ?? 0} bindings ·{" "}
							{selectedConcept.source_citations?.length ?? 0} citations ·{" "}
							{selectedConcept.mcp_evidence?.length ?? 0} receipts
						</div>
						{(selectedConcept.attributes ?? []).map((a) => (
							<div key={a.name} className="card-cream row-between">
								<span className="mono text-sm">
									{a.name} ({a.sql_type_hint}){a.is_business_key ? " · BK" : ""}
									{a.is_pii ? " · PII" : ""}
								</span>
								<button
									type="button"
									className="btn btn-sm"
									disabled={lockedByYaml}
									onClick={() =>
										onStageOps([
											{
												op_type: "UpdateAttributeMetadata",
												concept_uri: selectedConcept.concept_uri,
												attribute_name: a.name,
												is_pii: !a.is_pii,
												rationale: "Toggle PII",
											},
										])
									}
								>
									{a.is_pii ? "Unmark PII" : "Mark PII"}
								</button>
							</div>
						))}
						{!isMobile && (
							<form
								className="stack-sm"
								onSubmit={(e) => {
									e.preventDefault();
									if (lockedByYaml || !attr.name.trim()) return;
									onStageOps([
										{
											op_type: "AddAttribute",
											concept_uri: selectedConcept.concept_uri,
											rationale: "Mode 1 attr",
											attribute: {
												name: attr.name.trim(),
												logical_type: attr.type,
												sql_type_hint: attr.hint.trim() || "VARCHAR",
												is_business_key: attr.bk,
												is_pii: attr.pii,
												nullable: !attr.bk,
											},
										},
									]);
									setAttr((p) => ({ ...p, name: "", bk: false, pii: false }));
								}}
							>
								<div className="grid-2">
									<input
										type="text"
										className="input"
										disabled={lockedByYaml}
										placeholder="Attribute name..."
										value={attr.name}
										onChange={(e) =>
											setAttr((p) => ({ ...p, name: e.target.value }))
										}
									/>
									<input
										type="text"
										className="input"
										disabled={lockedByYaml}
										placeholder="SQL hint"
										value={attr.hint}
										onChange={(e) =>
											setAttr((p) => ({ ...p, hint: e.target.value }))
										}
									/>
								</div>
								<div className="row">
									<select
										className="select"
										style={{ width: "auto" }}
										disabled={lockedByYaml}
										value={attr.type}
										onChange={(e) =>
											setAttr((p) => ({
												...p,
												type: e.target.value as Extract<
													PortableLogicalType,
													string
												>,
											}))
										}
									>
										{ATTR_TYPES.map((t) => (
											<option key={t} value={t}>
												{t}
											</option>
										))}
									</select>
									<label className="radio-row">
										<input
											type="checkbox"
											disabled={lockedByYaml}
											checked={attr.bk}
											onChange={(e) =>
												setAttr((p) => ({ ...p, bk: e.target.checked }))
											}
										/>
										<span>BK</span>
									</label>
									<label className="radio-row">
										<input
											type="checkbox"
											disabled={lockedByYaml}
											checked={attr.pii}
											onChange={(e) =>
												setAttr((p) => ({ ...p, pii: e.target.checked }))
											}
										/>
										<span>PII</span>
									</label>
									<button
										type="submit"
										className="btn btn-sm btn-secondary"
										disabled={lockedByYaml || !attr.name.trim()}
									>
										+ Add Attr
									</button>
								</div>
							</form>
						)}
					</div>
				)}
			</section>

			<section className="card-cream stack-sm">
				<h2 className="h2-serif">
					Uncommitted Step 6 Proposals ({visibleProposals.length})
				</h2>
				{visibleProposals.length === 0 ? (
					<p className="text-muted text-sm">
						No pending Steward alignment proposals.
					</p>
				) : (
					visibleProposals.map((p) => (
						<div key={p.proposal_id} className="card-subtle stack-sm">
							<div className="row-between">
								<span className="mono text-sm font-semibold">
									{p.proposal_id}
								</span>
								<span className="pill pill-warn">{p.status}</span>
							</div>
							<p className="text-sm">{p.summary}</p>
							<div className="row">
								<button
									type="button"
									className="btn btn-primary btn-sm"
									disabled={lockedByYaml}
									onClick={() =>
										onStageOps(
											p.delta_ops as unknown as OntologyDelta[],
											p.proposal_id,
										)
									}
								>
									Stage into Draft Ontology
								</button>
								<button
									type="button"
									className="btn btn-secondary btn-sm"
									onClick={() => onDismissProposal(p.proposal_id)}
								>
									Dismiss Proposal
								</button>
								<button
									type="button"
									className="btn btn-ghost btn-sm"
									title="Hides from this browser session only; uncommitted proposals remain PENDING_STEWARD on the server and reappear after reload until staged or dismissed."
									onClick={() => onHideProposal(p.proposal_id)}
								>
									Hide in Session
								</button>
							</div>
						</div>
					))
				)}
			</section>
		</div>
	);
}

export function Step5CatalogKeyTier({
	onStageOp,
}: { onStageOp: (op: OntologyDelta) => void }) {
	const bundle = useProjectStore((s) => s.ontologyBundle);
	const ont = useProjectStore((s) => s.ontologyState);
	const proj = useProjectStore((s) => s.activeProject);

	const lockedByYaml = ont.draftChannel === "yaml_text";
	const working = ont.workingOntology ?? bundle?.ontology;
	const locks = bundle?.evolution_locks ?? null;
	const ns =
		working?.domain_namespace ?? proj?.domain_namespace ?? "enterprise";

	const [renames, setRenames] = useState<Record<string, string>>({});
	const [bkccs, setBkccs] = useState<Record<string, string>>({});
	const [ef, setEf] = useState({ name: "", table: "", desc: "" });
	const [cf, setCf] = useState({ target: "", key: "", desc: "" });

	const concepts = working?.concepts ?? [];
	const enums = working?.enumerations ?? [];

	return (
		<div className="stack-md">
			{lockedByYaml && (
				<output className="callout-amber">
					<strong>Mode 2 Read-Only:</strong> Raw YAML edits active in Mode 3.
					Commit or discard YAML first.
				</output>
			)}

			<div className="banner">
				Configure 3-Tier Business Key Authority (<code>KeyTierEnum</code> &amp;{" "}
				<code>BKCC</code>/<code>SOR</code>), renames, unlocked deletes, and{" "}
				<code>ref_*</code> enumerations. Multi-concept merges edit in Mode 3
				YAML.
			</div>

			<section className="card-subtle stack-sm">
				<h2 className="h2-serif">Concept Catalog &amp; Key Authority Tiers</h2>
				<div className="table-scroll-region">
					<table className="responsive-table">
						<thead>
							<tr>
								<th scope="col">Concept</th>
								<th scope="col">Key Authority Tier</th>
								<th scope="col">BKCC / SOR Default</th>
								<th scope="col">Rename</th>
								<th scope="col">Actions</th>
							</tr>
						</thead>
						<tbody>
							{concepts.map((c) => {
								const locked = isConceptLockedInEvolution(c, locks);
								const bkcc = bkccs[c.concept_uri] ?? c.bkcc_default ?? "";
								const ren = renames[c.concept_uri] ?? c.concept_name;
								const ro = lockedByYaml || locked;
								return (
									<tr key={c.concept_uri}>
										<th scope="row">
											<div>{c.concept_name}</div>
											<div className="mono text-sm text-muted">
												{c.concept_uri}
											</div>
											{locked && (
												<span className="pill pill-dark">
													LOCK-W
													{String(locks?.wave_number ?? 1).padStart(3, "0")}
												</span>
											)}
										</th>
										<td data-label="Key Tier">
											<select
												className="select"
												aria-label={`Key tier for ${c.concept_name}`}
												disabled={ro}
												value={c.key_authority_tier}
												onChange={(ev) =>
													onStageOp({
														op_type: "SetConceptKeyAuthority",
														concept_uri: c.concept_uri,
														key_authority_tier: ev.target.value as KeyTierEnum,
														bkcc_default: bkcc.trim() || null,
														rationale: "Mode 2 tier",
													})
												}
											>
												{KEY_TIERS.map(([v, l]) => (
													<option key={v} value={v}>
														{l}
													</option>
												))}
											</select>
										</td>
										<td data-label="BKCC">
											<div className="row">
												<input
													type="text"
													className="input"
													aria-label={`BKCC default for ${c.concept_name}`}
													disabled={ro}
													placeholder="IBOR_CORE"
													value={bkcc}
													onChange={(ev) =>
														setBkccs((p) => ({
															...p,
															[c.concept_uri]: ev.target.value,
														}))
													}
												/>
												<button
													type="button"
													className="btn btn-sm"
													disabled={ro}
													onClick={() =>
														onStageOp({
															op_type: "SetConceptKeyAuthority",
															concept_uri: c.concept_uri,
															key_authority_tier: c.key_authority_tier,
															bkcc_default: bkcc.trim() || null,
															rationale: "Mode 2 BKCC",
														})
													}
												>
													Apply BKCC
												</button>
											</div>
										</td>
										<td data-label="Rename">
											<div className="row">
												<input
													type="text"
													className="input"
													aria-label={`Rename ${c.concept_name}`}
													disabled={ro}
													value={ren}
													onChange={(ev) =>
														setRenames((p) => ({
															...p,
															[c.concept_uri]: ev.target.value,
														}))
													}
												/>
												<button
													type="button"
													className="btn btn-sm"
													disabled={
														ro || !ren.trim() || ren.trim() === c.concept_name
													}
													onClick={() =>
														onStageOp({
															op_type: "RenameConcept",
															concept_uri: c.concept_uri,
															new_concept_name: ren.trim(),
															rationale: "Mode 2 rename",
														})
													}
												>
													Rename
												</button>
											</div>
										</td>
										<td data-label="Actions">
											<button
												type="button"
												className="btn btn-sm btn-danger"
												disabled={ro}
												onClick={() =>
													onStageOp({
														op_type: "DeleteConcept",
														concept_uri: c.concept_uri,
														rationale: "Mode 2 delete",
													})
												}
											>
												{locked ? "Locked" : "Delete"}
											</button>
										</td>
									</tr>
								);
							})}
						</tbody>
					</table>
				</div>
			</section>

			<section className="split-grid">
				<div className="card-subtle stack-sm">
					<h2 className="h2-serif">Reference Enumerations (Zone Z0 ref_*)</h2>
					{enums.length === 0 ? (
						<p className="text-muted text-sm">
							No reference enumerations modeled yet.
						</p>
					) : (
						enums.map((en) => (
							<div key={en.enum_uri} className="card-cream stack-sm">
								<div className="row-between">
									<div>
										<strong>{en.enumeration_name}</strong>{" "}
										<span className="mono text-sm text-muted">
											{en.target_reference_table}
										</span>
									</div>
									<button
										type="button"
										className="btn btn-sm btn-danger"
										disabled={lockedByYaml}
										onClick={() =>
											onStageOp({
												op_type: "DeleteEnumeration",
												enum_uri: en.enum_uri,
												rationale: "Delete enum",
											})
										}
									>
										Delete Enum
									</button>
								</div>
								<div className="row">
									{Object.entries(en.codes).map(([k, v]) => (
										<span key={k} className="pill">
											<span className="mono">{k}</span>: {v}
											<button
												type="button"
												className="btn btn-ghost btn-sm"
												disabled={lockedByYaml}
												aria-label={`Remove code ${k} from ${en.enumeration_name}`}
												onClick={() =>
													onStageOp({
														op_type: "RemoveEnumerationCode",
														enum_uri: en.enum_uri,
														code: k,
														rationale: "Remove code",
													})
												}
											>
												×
											</button>
										</span>
									))}
								</div>
							</div>
						))
					)}
				</div>

				<div className="stack-md">
					<form
						className="card-subtle stack-sm"
						onSubmit={(e) => {
							e.preventDefault();
							if (lockedByYaml || !ef.name.trim()) return;
							const slug = ef.name
								.trim()
								.toLowerCase()
								.replace(/[^a-z0-9_]+/g, "_");
							onStageOp({
								op_type: "AddEnumeration",
								rationale: "Add enum",
								enumeration: {
									enum_uri: `urn:opendv:${ns}:enum:${slug}`,
									enumeration_name: ef.name.trim(),
									description:
										ef.desc.trim() || `Reference codes for ${ef.name.trim()}`,
									target_reference_table: ef.table.trim() || `ref_${slug}`,
									codes: {},
								},
							});
							setEf({ name: "", table: "", desc: "" });
						}}
					>
						<h3 className="font-semibold">Add Reference Enumeration</h3>
						<input
							type="text"
							className="input"
							disabled={lockedByYaml}
							placeholder="Enumeration Name (e.g. BallotStatus)"
							value={ef.name}
							onChange={(e) => setEf((p) => ({ ...p, name: e.target.value }))}
						/>
						<input
							type="text"
							className="input"
							disabled={lockedByYaml}
							placeholder="ref_ballot_status"
							value={ef.table}
							onChange={(e) => setEf((p) => ({ ...p, table: e.target.value }))}
						/>
						<input
							type="text"
							className="input"
							disabled={lockedByYaml}
							placeholder="Description"
							value={ef.desc}
							onChange={(e) => setEf((p) => ({ ...p, desc: e.target.value }))}
						/>
						<button
							type="submit"
							className="btn btn-primary btn-sm"
							disabled={lockedByYaml || !ef.name.trim()}
						>
							Add Enumeration
						</button>
					</form>

					<form
						className="card-subtle stack-sm"
						onSubmit={(e) => {
							e.preventDefault();
							const target = cf.target || enums[0]?.enum_uri || "";
							if (lockedByYaml || !target || !cf.key.trim()) return;
							onStageOp({
								op_type: "AddEnumerationCode",
								enum_uri: target,
								code: cf.key.trim(),
								description: cf.desc.trim() || cf.key.trim(),
								rationale: "Add code",
							});
							setCf((p) => ({ ...p, key: "", desc: "" }));
						}}
					>
						<h3 className="font-semibold">Add Enumeration Code</h3>
						<select
							className="select"
							disabled={lockedByYaml || enums.length === 0}
							value={cf.target || enums[0]?.enum_uri || ""}
							onChange={(e) => setCf((p) => ({ ...p, target: e.target.value }))}
						>
							{enums.map((en) => (
								<option key={en.enum_uri} value={en.enum_uri}>
									{en.enumeration_name}
								</option>
							))}
						</select>
						<div className="grid-2">
							<input
								type="text"
								className="input"
								disabled={lockedByYaml || enums.length === 0}
								placeholder="Code"
								value={cf.key}
								onChange={(e) => setCf((p) => ({ ...p, key: e.target.value }))}
							/>
							<input
								type="text"
								className="input"
								disabled={lockedByYaml || enums.length === 0}
								placeholder="Label"
								value={cf.desc}
								onChange={(e) => setCf((p) => ({ ...p, desc: e.target.value }))}
							/>
						</div>
						<button
							type="submit"
							className="btn btn-secondary btn-sm"
							disabled={lockedByYaml || enums.length === 0 || !cf.key.trim()}
						>
							Add Code
						</button>
					</form>
				</div>
			</section>
		</div>
	);
}
