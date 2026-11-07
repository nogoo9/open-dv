import type { ChangeEvent } from "react";
import { EXPLORER_COLORS, EXPLORER_FONTS } from "../design-tokens";

export type StewardHoldPolicyOption =
	| "pause"
	| "auto_approve_top_candidate"
	| "reject_on_hold";

export interface NewRunFormFieldsProps {
	specPath: string;
	onSpecPathChange: (val: string) => void;
	specFileName?: string | undefined;
	onSpecFileClear: () => void;
	onSpecFileUpload: (e: ChangeEvent<HTMLInputElement>) => void;
	contractPath: string;
	onContractPathChange: (val: string) => void;
	contractFileName?: string | undefined;
	onContractFileClear: () => void;
	onContractFileUpload: (e: ChangeEvent<HTMLInputElement>) => void;
	waveId: string;
	onWaveIdChange: (val: string) => void;
	holdPolicy: StewardHoldPolicyOption;
	onHoldPolicyChange: (val: StewardHoldPolicyOption) => void;
}

export function NewRunFormFields({
	specPath,
	onSpecPathChange,
	specFileName,
	onSpecFileClear,
	onSpecFileUpload,
	contractPath,
	onContractPathChange,
	contractFileName,
	onContractFileClear,
	onContractFileUpload,
	waveId,
	onWaveIdChange,
	holdPolicy,
	onHoldPolicyChange,
}: NewRunFormFieldsProps) {
	return (
		<>
			{/* Spec Input */}
			<div style={{ display: "flex", flexDirection: "column", gap: "6px" }}>
				<label
					htmlFor="input-spec-path"
					style={{
						fontWeight: 500,
						fontSize: "12px",
						color: EXPLORER_COLORS.inkMuted,
					}}
				>
					Input Specification Path
				</label>
				<div style={{ display: "flex", gap: "8px" }}>
					<input
						id="input-spec-path"
						type="text"
						data-testid="input-spec-path"
						value={specPath}
						onChange={(e) => onSpecPathChange(e.target.value)}
						placeholder="Path to pipeline input specification (e.g. spec.yaml)"
						style={{
							flex: 1,
							padding: "6px 10px",
							fontFamily: EXPLORER_FONTS.mono,
							fontSize: "12px",
							border: `1px solid ${EXPLORER_COLORS.borderLine}`,
							borderRadius: "4px",
						}}
					/>
					<label
						htmlFor="upload-spec-file"
						data-testid="btn-upload-spec"
						style={{
							cursor: "pointer",
							padding: "6px 10px",
							fontSize: "12px",
							fontWeight: 500,
							border: `1px solid ${EXPLORER_COLORS.borderLine}`,
							borderRadius: "4px",
							background: EXPLORER_COLORS.bgSubtle,
							color: EXPLORER_COLORS.inkBody,
							display: "flex",
							alignItems: "center",
						}}
					>
						Upload
						<input
							id="upload-spec-file"
							type="file"
							data-testid="upload-spec-file"
							accept=".yaml,.yml,.json"
							onChange={onSpecFileUpload}
							style={{ display: "none" }}
						/>
					</label>
				</div>
				{specFileName && (
					<div
						style={{
							fontSize: "11px",
							color: EXPLORER_COLORS.glyphData,
							display: "flex",
							alignItems: "center",
							gap: "6px",
						}}
					>
						<span>Attached:</span>
						<strong data-testid="spec-file-name">{specFileName}</strong>
						<button
							type="button"
							onClick={onSpecFileClear}
							style={{
								all: "unset",
								cursor: "pointer",
								color: EXPLORER_COLORS.inkMuted,
								textDecoration: "underline",
							}}
						>
							remove
						</button>
					</div>
				)}
			</div>

			{/* Initial Contract Input */}
			<div style={{ display: "flex", flexDirection: "column", gap: "6px" }}>
				<label
					htmlFor="input-contract-path"
					style={{
						fontWeight: 500,
						fontSize: "12px",
						color: EXPLORER_COLORS.inkMuted,
					}}
				>
					Initial Contract Path (Optional, GAP-07)
				</label>
				<div style={{ display: "flex", gap: "8px" }}>
					<input
						id="input-contract-path"
						type="text"
						data-testid="input-contract-path"
						value={contractPath}
						onChange={(e) => onContractPathChange(e.target.value)}
						placeholder="Optional starting contract path"
						style={{
							flex: 1,
							padding: "6px 10px",
							fontFamily: EXPLORER_FONTS.mono,
							fontSize: "12px",
							border: `1px solid ${EXPLORER_COLORS.borderLine}`,
							borderRadius: "4px",
						}}
					/>
					<label
						htmlFor="upload-contract-file"
						data-testid="btn-upload-contract"
						style={{
							cursor: "pointer",
							padding: "6px 10px",
							fontSize: "12px",
							fontWeight: 500,
							border: `1px solid ${EXPLORER_COLORS.borderLine}`,
							borderRadius: "4px",
							background: EXPLORER_COLORS.bgSubtle,
							color: EXPLORER_COLORS.inkBody,
							display: "flex",
							alignItems: "center",
						}}
					>
						Upload
						<input
							id="upload-contract-file"
							type="file"
							data-testid="upload-contract-file"
							accept=".yaml,.yml"
							onChange={onContractFileUpload}
							style={{ display: "none" }}
						/>
					</label>
				</div>
				{contractFileName && (
					<div
						style={{
							fontSize: "11px",
							color: EXPLORER_COLORS.glyphData,
							display: "flex",
							alignItems: "center",
							gap: "6px",
						}}
					>
						<span>Attached:</span>
						<strong data-testid="contract-file-name">{contractFileName}</strong>
						<button
							type="button"
							onClick={onContractFileClear}
							style={{
								all: "unset",
								cursor: "pointer",
								color: EXPLORER_COLORS.inkMuted,
								textDecoration: "underline",
							}}
						>
							remove
						</button>
					</div>
				)}
			</div>

			{/* Target Wave & Hold Policy */}
			<div style={{ display: "flex", gap: "12px" }}>
				<div
					style={{
						flex: 1,
						display: "flex",
						flexDirection: "column",
						gap: "6px",
					}}
				>
					<label
						htmlFor="select-wave-id"
						style={{
							fontWeight: 500,
							fontSize: "12px",
							color: EXPLORER_COLORS.inkMuted,
						}}
					>
						Target Wave
					</label>
					<select
						id="select-wave-id"
						data-testid="select-wave-id"
						value={waveId}
						onChange={(e) => onWaveIdChange(e.target.value)}
						style={{
							padding: "6px 10px",
							fontSize: "12px",
							border: `1px solid ${EXPLORER_COLORS.borderLine}`,
							borderRadius: "4px",
							background: EXPLORER_COLORS.bgSurface,
						}}
					>
						<option value="">All Waves (Full Pipeline)</option>
						<option value="W0">W0 · Initial Profiling</option>
						<option value="W1">W1 · Core Entities (Hubs/Links)</option>
						<option value="W2">W2 · Satellite Expansion</option>
						<option value="W3">W3 · Unstructured Docs</option>
					</select>
				</div>

				<div
					style={{
						flex: 1,
						display: "flex",
						flexDirection: "column",
						gap: "6px",
					}}
				>
					<label
						htmlFor="select-hold-policy"
						style={{
							fontWeight: 500,
							fontSize: "12px",
							color: EXPLORER_COLORS.inkMuted,
						}}
					>
						Steward Hold Policy
					</label>
					<select
						id="select-hold-policy"
						data-testid="select-hold-policy"
						value={holdPolicy}
						onChange={(e) =>
							onHoldPolicyChange(e.target.value as StewardHoldPolicyOption)
						}
						style={{
							padding: "6px 10px",
							fontSize: "12px",
							border: `1px solid ${EXPLORER_COLORS.borderLine}`,
							borderRadius: "4px",
							background: EXPLORER_COLORS.bgSurface,
						}}
					>
						<option value="pause">Pause on Hold (Manual Steward Review)</option>
						<option value="auto_approve_top_candidate">
							Auto-Approve Top Candidate
						</option>
						<option value="reject_on_hold">Reject on Hold</option>
					</select>
				</div>
			</div>
		</>
	);
}
