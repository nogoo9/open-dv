/**
 * Interactive Pipeline Specification Form (`InteractiveSpecForm.tsx`)
 *
 * Semantic form with `<fieldset>` / `<legend>` groupings for defining
 * an Open-DV Agentic Pipeline (`PipelineInputSpec`) interactively.
 */

import type { CSSProperties } from "react";
import { EXPLORER_COLORS, EXPLORER_FONTS } from "../design-tokens";
import type { StewardHoldPolicyOption } from "../modals/NewRunFormFields";
import {
	type InteractivePipelineDraft,
	type InteractiveWaveDocDraft,
	type InteractiveWaveDraft,
	type InteractiveWaveSourceDraft,
	type OntologyPolicyOption,
	type WaveTypeOption,
	createEmptyDocDraft,
	createEmptySourceDraft,
	createEmptyWaveDraft,
	draftToYamlOrJsonText,
} from "./specBuilderUtils";

export interface InteractiveSpecFormProps {
	draft: InteractivePipelineDraft;
	onChange: (next: InteractivePipelineDraft) => void;
}

const INPUT_STYLE: CSSProperties = {
	padding: "6px 10px",
	fontSize: "12px",
	border: `1px solid ${EXPLORER_COLORS.borderLine}`,
	borderRadius: "4px",
	background: EXPLORER_COLORS.bgSurface,
	color: EXPLORER_COLORS.inkPrimary,
	fontFamily: EXPLORER_FONTS.sans,
	width: "100%",
	boxSizing: "border-box",
};

const MONO_INPUT_STYLE: CSSProperties = {
	...INPUT_STYLE,
	fontFamily: EXPLORER_FONTS.mono,
};

const LABEL_STYLE: CSSProperties = {
	fontWeight: 500,
	fontSize: "11.5px",
	color: EXPLORER_COLORS.inkMuted,
	marginBottom: "3px",
	display: "block",
};

const FIELDSET_STYLE: CSSProperties = {
	border: `1px solid ${EXPLORER_COLORS.borderLine}`,
	borderRadius: "6px",
	padding: "14px 16px",
	margin: 0,
	backgroundColor: EXPLORER_COLORS.bgSurface,
};

const LEGEND_STYLE: CSSProperties = {
	fontWeight: 600,
	fontSize: "12.5px",
	padding: "0 6px",
	color: EXPLORER_COLORS.inkPrimary,
};

interface CodeFieldProps {
	id: string;
	name: string;
	label: string;
	testId: string;
	value: string;
	onChange: (val: string) => void;
	placeholder?: string | undefined;
	mono?: boolean | undefined;
}

function TextField({
	id,
	name,
	label,
	testId,
	value,
	onChange,
	placeholder,
	mono = true,
}: CodeFieldProps) {
	return (
		<div>
			<label htmlFor={id} style={LABEL_STYLE}>
				{label}
			</label>
			<input
				id={id}
				name={name}
				type="text"
				data-testid={testId}
				autoCorrect={mono ? "off" : undefined}
				autoCapitalize={mono ? "off" : undefined}
				spellCheck={mono ? false : undefined}
				value={value}
				placeholder={placeholder}
				onChange={(e) => onChange(e.target.value)}
				style={mono ? MONO_INPUT_STYLE : INPUT_STYLE}
			/>
		</div>
	);
}

export function InteractiveSpecForm({
	draft,
	onChange,
}: InteractiveSpecFormProps) {
	const updateField = <K extends keyof InteractivePipelineDraft>(
		key: K,
		value: InteractivePipelineDraft[K],
	) => onChange({ ...draft, [key]: value });

	const updateWave = (wIdx: number, patch: Partial<InteractiveWaveDraft>) => {
		onChange({
			...draft,
			waves: draft.waves.map((w, i) => (i === wIdx ? { ...w, ...patch } : w)),
		});
	};

	const updateSource = (
		wIdx: number,
		sIdx: number,
		patch: Partial<InteractiveWaveSourceDraft>,
	) => {
		const wave = draft.waves[wIdx];
		if (!wave) return;
		updateWave(wIdx, {
			sources: wave.sources.map((s, i) =>
				i === sIdx ? { ...s, ...patch } : s,
			),
		});
	};

	const updateDoc = (
		wIdx: number,
		dIdx: number,
		patch: Partial<InteractiveWaveDocDraft>,
	) => {
		const wave = draft.waves[wIdx];
		if (!wave) return;
		updateWave(wIdx, {
			unstructuredDocuments: wave.unstructuredDocuments.map((d, i) =>
				i === dIdx ? { ...d, ...patch } : d,
			),
		});
	};

	return (
		<div
			data-testid="interactive-spec-form"
			style={{ display: "flex", flexDirection: "column", gap: "16px" }}
		>
			<fieldset style={FIELDSET_STYLE}>
				<legend style={LEGEND_STYLE}>Project & Ontology Configuration</legend>
				<div
					style={{
						display: "grid",
						gridTemplateColumns: "repeat(auto-fit, minmax(220px, 1fr))",
						gap: "12px",
					}}
				>
					<TextField
						id="builder-input-pipeline-id"
						name="pipeline_id"
						label="Pipeline ID"
						testId="builder-input-pipeline-id"
						value={draft.pipelineId}
						onChange={(v) => updateField("pipelineId", v)}
					/>
					<TextField
						id="builder-input-project-id"
						name="project_id"
						label="Project ID"
						testId="builder-input-project-id"
						value={draft.projectId}
						onChange={(v) => updateField("projectId", v)}
					/>
					<TextField
						id="builder-input-display-name"
						name="display_name"
						label="Display Name"
						testId="builder-input-display-name"
						value={draft.displayName}
						mono={false}
						onChange={(v) => updateField("displayName", v)}
					/>
					<TextField
						id="builder-input-domain-namespace"
						name="domain_namespace"
						label="Domain Namespace"
						testId="builder-input-domain-namespace"
						value={draft.domainNamespace}
						onChange={(v) => updateField("domainNamespace", v)}
					/>
				</div>

				<div
					style={{
						display: "grid",
						gridTemplateColumns: "repeat(auto-fit, minmax(260px, 1fr))",
						gap: "12px",
						marginTop: "12px",
					}}
				>
					<TextField
						id="builder-input-graph-a-path"
						name="graph_a_ontology_path"
						label="Graph A Ontology Path"
						testId="builder-input-graph-a-path"
						value={draft.graphAOntologyPath}
						onChange={(v) => updateField("graphAOntologyPath", v)}
					/>
					<TextField
						id="builder-input-initial-contract-path"
						name="initial_contract_path"
						label="Initial Contract Path (Optional, GAP-07)"
						testId="builder-input-initial-contract-path"
						value={draft.initialContractPath}
						placeholder="Optional starting contract YAML path"
						onChange={(v) => updateField("initialContractPath", v)}
					/>
				</div>

				<div
					style={{
						display: "grid",
						gridTemplateColumns: "repeat(auto-fit, minmax(180px, 1fr))",
						gap: "12px",
						marginTop: "12px",
					}}
				>
					<div>
						<label htmlFor="builder-select-wave-id" style={LABEL_STYLE}>
							Target Wave Execution
						</label>
						<select
							id="builder-select-wave-id"
							name="wave_id"
							data-testid="builder-select-wave-id"
							value={draft.waveId}
							onChange={(e) => updateField("waveId", e.target.value)}
							style={INPUT_STYLE}
						>
							<option value="">All Waves (Full Pipeline)</option>
							<option value="W0">W0 · Initial Profiling</option>
							<option value="W1">W1 · Core Entities</option>
							<option value="W2">W2 · Satellite Expansion</option>
							<option value="W3">W3 · Unstructured Docs</option>
						</select>
					</div>
					<div>
						<label htmlFor="builder-select-hold-policy" style={LABEL_STYLE}>
							Steward Hold Policy
						</label>
						<select
							id="builder-select-hold-policy"
							name="steward_hold_policy"
							data-testid="builder-select-hold-policy"
							value={draft.stewardHoldPolicy}
							onChange={(e) =>
								updateField(
									"stewardHoldPolicy",
									e.target.value as StewardHoldPolicyOption,
								)
							}
							style={INPUT_STYLE}
						>
							<option value="pause">Pause on Hold (Manual Review)</option>
							<option value="auto_approve_top_candidate">
								Auto-Approve Top Candidate
							</option>
							<option value="reject_on_hold">Reject on Hold</option>
						</select>
					</div>
					<div>
						<label htmlFor="builder-select-ontology-policy" style={LABEL_STYLE}>
							Ontology Policy
						</label>
						<select
							id="builder-select-ontology-policy"
							name="ontology_policy"
							data-testid="builder-select-ontology-policy"
							value={draft.ontologyPolicy}
							onChange={(e) =>
								updateField(
									"ontologyPolicy",
									e.target.value as OntologyPolicyOption,
								)
							}
							style={INPUT_STYLE}
						>
							<option value="off">Off (Strict Graph A)</option>
							<option value="recommend">Recommend Delta (Hold)</option>
							<option value="auto_accept">Auto-Accept Safe Delta</option>
						</select>
					</div>
				</div>
			</fieldset>

			<fieldset style={FIELDSET_STYLE}>
				<legend style={LEGEND_STYLE}>
					Interactive Waves & Sources Builder
				</legend>
				<div
					style={{
						display: "flex",
						justifyContent: "space-between",
						alignItems: "center",
						marginBottom: "12px",
					}}
				>
					<span style={{ fontSize: "12px", color: EXPLORER_COLORS.inkMuted }}>
						Configure onboarding waves, relational feeds, and unstructured docs.
					</span>
					<button
						type="button"
						data-testid="btn-builder-add-wave"
						onClick={() =>
							onChange({
								...draft,
								waves: [
									...draft.waves,
									createEmptyWaveDraft(draft.waves.length),
								],
							})
						}
						style={{
							padding: "5px 10px",
							fontSize: "12px",
							fontWeight: 500,
							border: `1px solid ${EXPLORER_COLORS.borderLine}`,
							borderRadius: "4px",
							background: EXPLORER_COLORS.bgSubtle,
							cursor: "pointer",
						}}
					>
						+ Add Wave
					</button>
				</div>

				<div style={{ display: "flex", flexDirection: "column", gap: "12px" }}>
					{draft.waves.map((wave, wIdx) => (
						<div
							key={`wave-${wave.waveIndex}-${wave.waveName}`}
							data-testid={`builder-wave-card-${wIdx}`}
							style={{
								padding: "12px",
								borderRadius: "6px",
								border: `1px solid ${EXPLORER_COLORS.borderLine}`,
								backgroundColor: EXPLORER_COLORS.bgPage,
								display: "flex",
								flexDirection: "column",
								gap: "10px",
							}}
						>
							<div
								style={{
									display: "flex",
									gap: "10px",
									alignItems: "flex-end",
									flexWrap: "wrap",
								}}
							>
								<div style={{ flex: 1, minWidth: "160px" }}>
									<TextField
										id={`builder-wave-name-${wIdx}`}
										name={`wave_name_${wIdx}`}
										label={`Wave ${wIdx} Name`}
										testId={`builder-wave-name-${wIdx}`}
										value={wave.waveName}
										onChange={(v) => updateWave(wIdx, { waveName: v })}
									/>
								</div>
								<div style={{ flex: 1, minWidth: "180px" }}>
									<label
										htmlFor={`builder-wave-type-${wIdx}`}
										style={LABEL_STYLE}
									>
										Wave Type
									</label>
									<select
										id={`builder-wave-type-${wIdx}`}
										name={`wave_type_${wIdx}`}
										data-testid={`builder-wave-type-${wIdx}`}
										value={wave.waveType}
										onChange={(e) =>
											updateWave(wIdx, {
												waveType: e.target.value as WaveTypeOption,
											})
										}
										style={INPUT_STYLE}
									>
										<option value="COLD_START">COLD_START</option>
										<option value="INCREMENTAL_SOURCE">
											INCREMENTAL_SOURCE
										</option>
										<option value="SCHEMA_DRIFT">SCHEMA_DRIFT</option>
										<option value="UNSTRUCTURED_ONBOARDING">
											UNSTRUCTURED_ONBOARDING
										</option>
									</select>
								</div>
								<button
									type="button"
									data-testid={`btn-builder-add-source-${wIdx}`}
									onClick={() =>
										updateWave(wIdx, {
											sources: [...wave.sources, createEmptySourceDraft()],
										})
									}
									style={{
										padding: "6px 10px",
										fontSize: "11.5px",
										border: `1px solid ${EXPLORER_COLORS.borderLine}`,
										borderRadius: "4px",
										background: EXPLORER_COLORS.bgSurface,
										cursor: "pointer",
									}}
								>
									+ Add Relational Source
								</button>
								<button
									type="button"
									data-testid={`btn-builder-add-doc-${wIdx}`}
									onClick={() =>
										updateWave(wIdx, {
											unstructuredDocuments: [
												...wave.unstructuredDocuments,
												createEmptyDocDraft(),
											],
										})
									}
									style={{
										padding: "6px 10px",
										fontSize: "11.5px",
										border: `1px solid ${EXPLORER_COLORS.borderLine}`,
										borderRadius: "4px",
										background: EXPLORER_COLORS.bgSurface,
										cursor: "pointer",
									}}
								>
									+ Add Unstructured Document
								</button>
								{draft.waves.length > 1 && (
									<button
										type="button"
										data-testid={`btn-builder-remove-wave-${wIdx}`}
										onClick={() =>
											onChange({
												...draft,
												waves: draft.waves
													.filter((_, i) => i !== wIdx)
													.map((w, idx) => ({ ...w, waveIndex: idx })),
											})
										}
										style={{
											padding: "6px 10px",
											fontSize: "11.5px",
											border: `1px solid ${EXPLORER_COLORS.borderLine}`,
											borderRadius: "4px",
											background: "#FEF2F2",
											color: "#991B1B",
											cursor: "pointer",
										}}
									>
										Remove Wave
									</button>
								)}
							</div>

							{wave.sources.map((src, sIdx) => (
								<div
									key={src._key ?? `src-${wIdx}-${sIdx}`}
									style={{
										display: "grid",
										gridTemplateColumns: "1fr 1fr 1.6fr 1fr 1fr auto",
										gap: "8px",
										alignItems: "end",
										padding: "8px",
										backgroundColor: EXPLORER_COLORS.bgSurface,
										borderRadius: "4px",
										border: `1px solid ${EXPLORER_COLORS.borderLine}`,
									}}
								>
									<TextField
										id={`src-sys-${wIdx}-${sIdx}`}
										name={`src_sys_${wIdx}_${sIdx}`}
										label="Source System"
										testId={`builder-source-system-${wIdx}-${sIdx}`}
										value={src.sourceSystem}
										onChange={(v) =>
											updateSource(wIdx, sIdx, { sourceSystem: v })
										}
									/>
									<TextField
										id={`src-tbl-${wIdx}-${sIdx}`}
										name={`src_tbl_${wIdx}_${sIdx}`}
										label="Table Name"
										testId={`builder-source-table-${wIdx}-${sIdx}`}
										value={src.tableName}
										onChange={(v) => updateSource(wIdx, sIdx, { tableName: v })}
									/>
									<TextField
										id={`src-path-${wIdx}-${sIdx}`}
										name={`src_path_${wIdx}_${sIdx}`}
										label="Data Path"
										testId={`builder-source-path-${wIdx}-${sIdx}`}
										value={src.dataPath}
										onChange={(v) => updateSource(wIdx, sIdx, { dataPath: v })}
									/>
									<TextField
										id={`src-hub-${wIdx}-${sIdx}`}
										name={`src_hub_${wIdx}_${sIdx}`}
										label="Target Hub"
										testId={`builder-source-hub-${wIdx}-${sIdx}`}
										value={src.targetHubName}
										onChange={(v) =>
											updateSource(wIdx, sIdx, { targetHubName: v })
										}
									/>
									<TextField
										id={`src-bks-${wIdx}-${sIdx}`}
										name={`src_bks_${wIdx}_${sIdx}`}
										label="Business Keys"
										testId={`builder-source-bks-${wIdx}-${sIdx}`}
										value={src.businessKeyColumns}
										onChange={(v) =>
											updateSource(wIdx, sIdx, { businessKeyColumns: v })
										}
									/>
									<button
										type="button"
										aria-label={`Remove source ${src.tableName || sIdx + 1}`}
										onClick={() =>
											updateWave(wIdx, {
												sources: wave.sources.filter((_, i) => i !== sIdx),
											})
										}
										style={{
											padding: "6px 8px",
											fontSize: "11px",
											border: `1px solid ${EXPLORER_COLORS.borderLine}`,
											borderRadius: "4px",
											background: EXPLORER_COLORS.bgSubtle,
											cursor: "pointer",
										}}
									>
										×
									</button>
								</div>
							))}

							{wave.unstructuredDocuments.map((doc, dIdx) => (
								<div
									key={doc._key ?? `doc-${wIdx}-${dIdx}`}
									style={{
										display: "grid",
										gridTemplateColumns: "1fr 2fr 1fr auto",
										gap: "8px",
										alignItems: "end",
										padding: "8px",
										backgroundColor: EXPLORER_COLORS.bgSurface,
										borderRadius: "4px",
										border: `1px solid ${EXPLORER_COLORS.borderLine}`,
									}}
								>
									<TextField
										id={`doc-id-${wIdx}-${dIdx}`}
										name={`doc_id_${wIdx}_${dIdx}`}
										label="Document ID"
										testId={`builder-doc-id-${wIdx}-${dIdx}`}
										value={doc.docId}
										onChange={(v) => updateDoc(wIdx, dIdx, { docId: v })}
									/>
									<TextField
										id={`doc-path-${wIdx}-${dIdx}`}
										name={`doc_path_${wIdx}_${dIdx}`}
										label="Document Path"
										testId={`builder-doc-path-${wIdx}-${dIdx}`}
										value={doc.documentPath}
										onChange={(v) => updateDoc(wIdx, dIdx, { documentPath: v })}
									/>
									<TextField
										id={`doc-hub-${wIdx}-${dIdx}`}
										name={`doc_hub_${wIdx}_${dIdx}`}
										label="Target Hub"
										testId={`builder-doc-hub-${wIdx}-${dIdx}`}
										value={doc.targetHubName}
										onChange={(v) =>
											updateDoc(wIdx, dIdx, { targetHubName: v })
										}
									/>
									<button
										type="button"
										aria-label={`Remove document ${doc.docId || dIdx + 1}`}
										onClick={() =>
											updateWave(wIdx, {
												unstructuredDocuments:
													wave.unstructuredDocuments.filter(
														(_, i) => i !== dIdx,
													),
											})
										}
										style={{
											padding: "6px 8px",
											fontSize: "11px",
											border: `1px solid ${EXPLORER_COLORS.borderLine}`,
											borderRadius: "4px",
											background: EXPLORER_COLORS.bgSubtle,
											cursor: "pointer",
										}}
									>
										×
									</button>
								</div>
							))}
						</div>
					))}
				</div>
			</fieldset>

			<fieldset style={FIELDSET_STYLE}>
				<legend style={LEGEND_STYLE}>
					Live Generated Specification Preview
				</legend>
				<pre
					data-testid="builder-live-spec-preview"
					style={{
						margin: 0,
						padding: "10px 12px",
						borderRadius: "4px",
						backgroundColor: EXPLORER_COLORS.codeBg,
						color: EXPLORER_COLORS.codeFg,
						fontFamily: EXPLORER_FONTS.mono,
						fontSize: "11.5px",
						maxHeight: "220px",
						overflowY: "auto",
					}}
				>
					{draftToYamlOrJsonText(draft)}
				</pre>
			</fieldset>
		</div>
	);
}
