/**
 * YAML / JSON Specification Upload & Inline Editor (`YamlSpecUploadEditor.tsx`)
 *
 * Allows uploading a `.yaml` / `.yml` / `.json` pipeline specification file,
 * specifying a workspace `input_spec_path`, or editing raw YAML/JSON text directly.
 *
 * Invariant: Uploading or editing ONLY stages the specification in local state;
 * it NEVER triggers a pipeline execution automatically.
 */

import type { CSSProperties, ChangeEvent } from "react";
import { EXPLORER_COLORS, EXPLORER_FONTS } from "../design-tokens";
import type { StewardHoldPolicyOption } from "../modals/NewRunFormFields";

export interface YamlSpecUploadEditorProps {
	specPath: string;
	onSpecPathChange: (val: string) => void;
	specContent: string;
	onSpecContentChange: (val: string) => void;
	fileName?: string | undefined;
	onFileNameChange: (val: string | undefined) => void;
	contractPath: string;
	onContractPathChange: (val: string) => void;
	contractContent?: string | undefined;
	onContractContentChange: (val: string | undefined) => void;
	contractFileName?: string | undefined;
	onContractFileNameChange: (val: string | undefined) => void;
	waveId: string;
	onWaveIdChange: (val: string) => void;
	holdPolicy: StewardHoldPolicyOption;
	onHoldPolicyChange: (val: StewardHoldPolicyOption) => void;
}

const INPUT_STYLE: CSSProperties = {
	padding: "6px 10px",
	fontSize: "12px",
	border: `1px solid ${EXPLORER_COLORS.borderLine}`,
	borderRadius: "4px",
	background: EXPLORER_COLORS.bgSurface,
	color: EXPLORER_COLORS.inkPrimary,
	fontFamily: EXPLORER_FONTS.sans,
};

const MONO_INPUT_STYLE: CSSProperties = {
	...INPUT_STYLE,
	fontFamily: EXPLORER_FONTS.mono,
};

const LABEL_STYLE: CSSProperties = {
	fontWeight: 500,
	fontSize: "11.5px",
	color: EXPLORER_COLORS.inkMuted,
	marginBottom: "4px",
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

export function YamlSpecUploadEditor({
	specPath,
	onSpecPathChange,
	specContent,
	onSpecContentChange,
	fileName,
	onFileNameChange,
	contractPath,
	onContractPathChange,
	onContractContentChange,
	contractFileName,
	onContractFileNameChange,
	waveId,
	onWaveIdChange,
	holdPolicy,
	onHoldPolicyChange,
}: YamlSpecUploadEditorProps) {
	const handleSpecUpload = (e: ChangeEvent<HTMLInputElement>) => {
		const file = e.target.files?.[0];
		if (!file) return;
		onFileNameChange(file.name);
		const inlineText = (file as unknown as { content?: string }).content;
		if (typeof inlineText === "string") {
			onSpecContentChange(inlineText);
			return;
		}
		if (typeof FileReader !== "undefined") {
			const reader = new FileReader();
			reader.onload = (evt) => {
				const text = evt.target?.result;
				if (typeof text === "string") {
					onSpecContentChange(text);
				}
			};
			reader.readAsText(file);
		} else if (typeof file.text === "function") {
			void file.text().then((text) => {
				onSpecContentChange(text);
			});
		}
	};

	const handleContractUpload = (e: ChangeEvent<HTMLInputElement>) => {
		const file = e.target.files?.[0];
		if (!file) return;
		onContractFileNameChange(file.name);
		const inlineText = (file as unknown as { content?: string }).content;
		if (typeof inlineText === "string") {
			onContractContentChange(inlineText);
			return;
		}
		if (typeof FileReader !== "undefined") {
			const reader = new FileReader();
			reader.onload = (evt) => {
				const text = evt.target?.result;
				if (typeof text === "string") {
					onContractContentChange(text);
				}
			};
			reader.readAsText(file);
		} else if (typeof file.text === "function") {
			void file.text().then((text) => {
				onContractContentChange(text);
			});
		}
	};

	const isStaged = Boolean(specContent.trim() || specPath.trim() || fileName);

	return (
		<div
			data-testid="yaml-spec-upload-editor"
			style={{ display: "flex", flexDirection: "column", gap: "16px" }}
		>
			<fieldset style={FIELDSET_STYLE}>
				<legend style={LEGEND_STYLE}>
					Pipeline Specification File or Path
				</legend>
				<div style={{ display: "flex", flexDirection: "column", gap: "10px" }}>
					<div>
						<label htmlFor="builder-yaml-spec-path" style={LABEL_STYLE}>
							Workspace Input Specification Path
						</label>
						<div style={{ display: "flex", gap: "8px", alignItems: "center" }}>
							<input
								id="builder-yaml-spec-path"
								name="input_spec_path"
								type="text"
								data-testid="builder-yaml-spec-path"
								autoCorrect="off"
								autoCapitalize="off"
								spellCheck={false}
								value={specPath}
								onChange={(e) => onSpecPathChange(e.target.value)}
								placeholder="e.g. examples/pipeline_samples/global_commerce_vault/pipeline_input.yaml"
								style={{ ...MONO_INPUT_STYLE, flex: 1 }}
							/>
							<label
								htmlFor="builder-upload-yaml-input"
								data-testid="btn-builder-upload-yaml"
								style={{
									cursor: "pointer",
									padding: "6px 12px",
									fontSize: "12px",
									fontWeight: 500,
									border: `1px solid ${EXPLORER_COLORS.borderLine}`,
									borderRadius: "4px",
									background: EXPLORER_COLORS.bgSubtle,
									color: EXPLORER_COLORS.inkPrimary,
									margin: 0,
								}}
							>
								Upload YAML / JSON
								<input
									id="builder-upload-yaml-input"
									name="spec_file"
									type="file"
									data-testid="builder-upload-yaml-input"
									accept=".yaml,.yml,.json"
									onChange={handleSpecUpload}
									style={{
										position: "absolute",
										clipPath: "inset(50%)",
										overflow: "hidden",
										width: "1px",
										height: "1px",
										margin: "-1px",
										padding: 0,
										border: 0,
										whiteSpace: "nowrap",
									}}
								/>
							</label>
						</div>
					</div>

					{fileName && (
						<div
							style={{
								fontSize: "11.5px",
								color: EXPLORER_COLORS.glyphData,
								display: "flex",
								alignItems: "center",
								gap: "8px",
							}}
						>
							<span>Uploaded file staged:</span>
							<strong data-testid="builder-uploaded-filename">
								{fileName}
							</strong>
							<button
								type="button"
								data-testid="btn-builder-clear-uploaded-file"
								onClick={() => {
									onFileNameChange(undefined);
								}}
								style={{
									background: "transparent",
									border: "none",
									padding: 0,
									font: "inherit",
									cursor: "pointer",
									color: EXPLORER_COLORS.inkMuted,
									textDecoration: "underline",
								}}
							>
								clear file badge
							</button>
						</div>
					)}

					<div>
						<label htmlFor="builder-yaml-textarea" style={LABEL_STYLE}>
							Inline YAML / JSON Specification Editor
						</label>
						<textarea
							id="builder-yaml-textarea"
							name="input_spec_content"
							data-testid="builder-yaml-textarea"
							autoCorrect="off"
							autoCapitalize="off"
							spellCheck={false}
							rows={12}
							value={specContent}
							onChange={(e) => onSpecContentChange(e.target.value)}
							placeholder="Paste or edit PipelineInputSpec YAML or JSON here..."
							style={{
								...MONO_INPUT_STYLE,
								width: "100%",
								boxSizing: "border-box",
								resize: "vertical",
								lineHeight: 1.45,
							}}
						/>
					</div>

					<div
						data-testid="builder-staged-indicator"
						aria-live="polite"
						style={{
							padding: "8px 12px",
							borderRadius: "4px",
							fontSize: "12px",
							fontWeight: 500,
							backgroundColor: isStaged ? "#F0FDF4" : EXPLORER_COLORS.bgSubtle,
							border: `1px solid ${isStaged ? "#BBF7D0" : EXPLORER_COLORS.borderLine}`,
							color: isStaged ? "#166534" : EXPLORER_COLORS.inkMuted,
						}}
					>
						{isStaged
							? "✓ Spec staged (ready to validate or run)"
							: "Stage a specification by uploading a file, entering a path, or pasting YAML/JSON above."}
					</div>
				</div>
			</fieldset>

			<fieldset style={FIELDSET_STYLE}>
				<legend style={LEGEND_STYLE}>
					Execution Options & Starting Contract
				</legend>
				<div
					style={{
						display: "grid",
						gridTemplateColumns: "repeat(auto-fit, minmax(220px, 1fr))",
						gap: "12px",
					}}
				>
					<div>
						<label htmlFor="builder-yaml-contract-path" style={LABEL_STYLE}>
							Initial Contract Path (Optional, GAP-07)
						</label>
						<div style={{ display: "flex", gap: "8px" }}>
							<input
								id="builder-yaml-contract-path"
								name="initial_contract_path"
								type="text"
								data-testid="builder-yaml-contract-path"
								autoCorrect="off"
								autoCapitalize="off"
								spellCheck={false}
								value={contractPath}
								onChange={(e) => onContractPathChange(e.target.value)}
								placeholder="Optional starting contract path"
								style={{ ...MONO_INPUT_STYLE, flex: 1 }}
							/>
							<label
								htmlFor="builder-upload-contract-input"
								style={{
									cursor: "pointer",
									padding: "6px 10px",
									fontSize: "12px",
									border: `1px solid ${EXPLORER_COLORS.borderLine}`,
									borderRadius: "4px",
									background: EXPLORER_COLORS.bgSubtle,
									margin: 0,
								}}
							>
								Upload
								<input
									id="builder-upload-contract-input"
									name="contract_file"
									type="file"
									accept=".yaml,.yml"
									onChange={handleContractUpload}
									style={{
										position: "absolute",
										clipPath: "inset(50%)",
										overflow: "hidden",
										width: "1px",
										height: "1px",
										margin: "-1px",
										padding: 0,
										border: 0,
										whiteSpace: "nowrap",
									}}
								/>
							</label>
						</div>
						{contractFileName && (
							<div
								style={{
									fontSize: "11px",
									color: EXPLORER_COLORS.glyphData,
									marginTop: "4px",
								}}
							>
								Attached contract: <strong>{contractFileName}</strong>
							</div>
						)}
					</div>

					<div>
						<label htmlFor="builder-yaml-wave-id" style={LABEL_STYLE}>
							Target Wave
						</label>
						<select
							id="builder-yaml-wave-id"
							name="wave_id"
							data-testid="builder-yaml-wave-id"
							value={waveId}
							onChange={(e) => onWaveIdChange(e.target.value)}
							style={{ ...INPUT_STYLE, width: "100%", boxSizing: "border-box" }}
						>
							<option value="">All Waves (Full Pipeline)</option>
							<option value="W0">W0 · Initial Profiling</option>
							<option value="W1">W1 · Core Entities</option>
							<option value="W2">W2 · Satellite Expansion</option>
							<option value="W3">W3 · Unstructured Docs</option>
						</select>
					</div>

					<div>
						<label htmlFor="builder-yaml-hold-policy" style={LABEL_STYLE}>
							Steward Hold Policy
						</label>
						<select
							id="builder-yaml-hold-policy"
							name="steward_hold_policy"
							data-testid="builder-yaml-hold-policy"
							value={holdPolicy}
							onChange={(e) =>
								onHoldPolicyChange(e.target.value as StewardHoldPolicyOption)
							}
							style={{ ...INPUT_STYLE, width: "100%", boxSizing: "border-box" }}
						>
							<option value="pause">Pause on Hold (Manual Review)</option>
							<option value="auto_approve_top_candidate">
								Auto-Approve Top Candidate
							</option>
							<option value="reject_on_hold">Reject on Hold</option>
						</select>
					</div>
				</div>
			</fieldset>
		</div>
	);
}
