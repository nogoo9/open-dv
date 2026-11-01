import type React from "react";
import { useState } from "react";

export interface PreHashPreset {
	readonly presetId: string;
	readonly label: string;
	readonly rawTokens: readonly string[];
	readonly typedTokens: readonly string[];
	readonly normalizedTokens: readonly string[];
	readonly lengthPrefixedTokens: readonly string[];
	readonly canonicalPreHashString: string;
	readonly sha256HexUpper: string;
	readonly dualProjectionPill: string;
	readonly collisionNote: string;
}

export interface PreHashStepperProps {
	readonly presets?: readonly PreHashPreset[];
	readonly initialPresetId?: string;
}

export const DEFAULT_PREHASH_PRESETS: readonly PreHashPreset[] = [
	{
		presetId: "calpers-lp-101",
		label: "CalPERS (GLOBAL · LP-101)",
		rawTokens: ["GLOBAL", "LP-101"],
		typedTokens: ["'GLOBAL'::VARCHAR", "'LP-101'::VARCHAR"],
		normalizedTokens: ["GLOBAL", "LP-101"],
		lengthPrefixedTokens: ["6:GLOBAL", "6:LP-101"],
		canonicalPreHashString: "6:GLOBAL||6:LP-101",
		sha256HexUpper:
			"5D15EB10C8D7621D3167849C41C5F9CD9244FD79FB720CB17104D49E4B820AE4",
		dualProjectionPill: "[HK: 5D15..0AE4 | GLOBAL:LP-101 CalPERS]",
		collisionNote:
			"Disjoint from Bavarian Pension LP-101 (6B7C..D8B9) via BKCC domain prefix GLOBAL.",
	},
	{
		presetId: "bavarian-lp-101",
		label: "Bavarian Pension (LEGACY_GP_ADMIN · LP-101)",
		rawTokens: ["LEGACY_GP_ADMIN", "LP-101"],
		typedTokens: ["'LEGACY_GP_ADMIN'::VARCHAR", "'LP-101'::VARCHAR"],
		normalizedTokens: ["LEGACY_GP_ADMIN", "LP-101"],
		lengthPrefixedTokens: ["15:LEGACY_GP_ADMIN", "6:LP-101"],
		canonicalPreHashString: "15:LEGACY_GP_ADMIN||6:LP-101",
		sha256HexUpper:
			"6B7C7180B62164F31F14EEBCD16CC5FD2B0CE58143DE66A4B67521113EC5D8B9",
		dualProjectionPill:
			"[HK: 6B7C..D8B9 | LEGACY_GP_ADMIN:LP-101 Bavarian Pension]",
		collisionNote:
			"Repaired by Gate 1 DV-COL-012 (Attempt k=2): 15-byte BKCC prefix isolates legacy European feed.",
	},
	{
		presetId: "apex-fund-iv",
		label: "Apex Buyout Fund IV (GLOBAL · APEX_BO_IV)",
		rawTokens: ["GLOBAL", "APEX_BO_IV"],
		typedTokens: ["'GLOBAL'::VARCHAR", "'APEX_BO_IV'::VARCHAR"],
		normalizedTokens: ["GLOBAL", "APEX_BO_IV"],
		lengthPrefixedTokens: ["6:GLOBAL", "10:APEX_BO_IV"],
		canonicalPreHashString: "6:GLOBAL||10:APEX_BO_IV",
		sha256HexUpper:
			"94A60FCDA278C5AEC284C595413935793D5CEC113C511F20690046061D388759",
		dualProjectionPill: "[HK: 94A6..8759 | GLOBAL:APEX_BO_IV Fund IV]",
		collisionNote: "Canonical Hub hash key for hub_fund (Apex Buyout Fund IV).",
	},
	{
		presetId: "delimiter-injection-proof",
		label: "Delimiter Injection Vector (['A||B', 'C'] vs ['A', 'B||C'])",
		rawTokens: ["A||B", "C"],
		typedTokens: ["'A||B'::VARCHAR", "'C'::VARCHAR"],
		normalizedTokens: ["A||B", "C"],
		lengthPrefixedTokens: ["4:A||B", "1:C"],
		canonicalPreHashString: "4:A||B||1:C",
		sha256HexUpper:
			"D9FDF36C4CA5DD63D8E37134B8CBE57FAE3D046EBB4F1F21D79327C52F59549C",
		dualProjectionPill:
			"[Injective Framing Proof: '4:A||B||1:C' != '1:A||4:B||C']",
		collisionNote:
			"Theorem 3.1 (DV-HSH-005): Length-prefixing guarantees injective tuple framing even when business keys contain '||'.",
	},
];

export function PreHashStepper({
	presets = DEFAULT_PREHASH_PRESETS,
	initialPresetId = "calpers-lp-101",
}: PreHashStepperProps): React.ReactElement {
	const [activePresetId, setActivePresetId] = useState<string>(initialPresetId);
	const activePreset =
		presets.find((p) => p.presetId === activePresetId) ?? presets[0];

	if (!activePreset) {
		return <div data-testid="prehash-stepper-empty">No presets configured</div>;
	}

	const stages = [
		{
			stageNumber: 1,
			title: "Stage 1 · Raw Source Business Key & BKCC Domain Tokens",
			formula: "(bkcc_prefix, bk_col_1, ..., bk_col_m)",
			output: `[${activePreset.rawTokens.map((t) => `"${t}"`).join(", ")}]`,
		},
		{
			stageNumber: 2,
			title: "Stage 2 · Deterministic Type Cast & NULL Sentinel Mapping",
			formula: "COALESCE(CAST(col AS VARCHAR), '\\x00NULL\\x00')",
			output: activePreset.typedTokens.join(", "),
		},
		{
			stageNumber: 3,
			title: "Stage 3 · Unicode NFC + Trim + Case Normalization",
			formula: "UPPER(TRIM(NFC(token)))",
			output: activePreset.normalizedTokens.join(" , "),
		},
		{
			stageNumber: 4,
			title: "Stage 4 · Injective Length-Prefix Token Framing",
			formula: "CAST(OCTET_LENGTH(t_i) AS VARCHAR) || ':' || t_i",
			output: activePreset.lengthPrefixedTokens.join(" , "),
		},
		{
			stageNumber: 5,
			title: "Stage 5 · Canonical Delimiter Concatenation",
			formula: "CONCAT_WS('||', framed_1, ..., framed_m)",
			output: activePreset.canonicalPreHashString,
		},
		{
			stageNumber: 6,
			title: "Stage 6 · SHA-256 Digest & Dual-Projection Pill (Rule §1.4)",
			formula: "UPPER(ENCODE(SHA256(UTF8(pre_hash)), 'hex'))",
			output: `${activePreset.sha256HexUpper}  ->  ${activePreset.dualProjectionPill}`,
		},
	];

	return (
		<div
			data-testid="prehash-stepper"
			style={{
				border: "1px solid var(--dv-border-subtle)",
				borderRadius: "8px",
				padding: "12px",
				background: "var(--dv-surface-1)",
			}}
		>
			<div
				style={{
					display: "flex",
					justifyContent: "space-between",
					alignItems: "center",
					flexWrap: "wrap",
					gap: "8px",
					marginBottom: "10px",
				}}
			>
				<div>
					<h4 style={{ margin: 0, fontSize: "14px" }}>
						Algorithm A.5 Interactive 6-Stage Pre-Hash Canonicalization Stepper
					</h4>
					<div style={{ fontSize: "12px", color: "var(--dv-text-muted)" }}>
						Verifies injective length-prefixed framing (<code>DV-HSH-001</code>{" "}
						– <code>DV-HSH-011</code>) &amp; human-readable dual-projection
						pills
					</div>
				</div>
				<div style={{ display: "flex", gap: "6px", flexWrap: "wrap" }}>
					{presets.map((preset) => (
						<button
							key={preset.presetId}
							type="button"
							data-testid={`prehash-preset-${preset.presetId}`}
							onClick={() => setActivePresetId(preset.presetId)}
							style={{
								padding: "4px 10px",
								borderRadius: "4px",
								border: "1px solid var(--dv-border-subtle)",
								background:
									activePreset.presetId === preset.presetId
										? "var(--dv-surface-2)"
										: "var(--dv-bg-canvas)",
								color: "var(--dv-text-primary)",
								fontFamily: "ui-monospace, monospace",
								fontSize: "11px",
								cursor: "pointer",
							}}
						>
							{preset.label}
						</button>
					))}
				</div>
			</div>

			<div
				style={{
					display: "grid",
					gridTemplateColumns: "repeat(auto-fit, minmax(280px, 1fr))",
					gap: "10px",
					marginBottom: "10px",
				}}
			>
				{stages.map((st) => (
					<div
						key={st.stageNumber}
						data-testid={`prehash-stage-${st.stageNumber}`}
						style={{
							padding: "10px",
							borderRadius: "6px",
							background: "var(--dv-bg-canvas)",
							border: "1px solid var(--dv-border-subtle)",
						}}
					>
						<div
							style={{ fontSize: "12px", fontWeight: 700, marginBottom: "4px" }}
						>
							{st.title}
						</div>
						<div
							style={{
								fontSize: "11px",
								fontFamily: "ui-monospace, monospace",
								color: "var(--dv-text-muted)",
								marginBottom: "6px",
							}}
						>
							Formula: {st.formula}
						</div>
						<div
							style={{
								fontSize: "12px",
								fontFamily: "ui-monospace, monospace",
								color: "var(--dv-gate-pass)",
								wordBreak: "break-all",
								fontWeight: 600,
							}}
						>
							{st.output}
						</div>
					</div>
				))}
			</div>

			<div
				data-testid="prehash-collision-note"
				style={{
					padding: "8px 10px",
					borderRadius: "6px",
					background: "var(--dv-surface-2)",
					border: "1px solid var(--dv-border-subtle)",
					fontSize: "12px",
				}}
			>
				<strong>Cryptographic &amp; Domain Guarantee:</strong>{" "}
				{activePreset.collisionNote}
			</div>
		</div>
	);
}
