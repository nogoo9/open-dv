import type React from "react";
import { INFERENCE_SCENARIOS } from "../data/site-data";

const S0 = "cold-start-w1";
const S1 = "multi-source-bkcc";
const S2 = "additive-ext-w2";
const S3 = "horizon-steer";
const ALL = `${S0} ${S1} ${S2} ${S3}`;
const S01 = `${S0} ${S1}`;
const S12 = `${S1} ${S2}`;
const S23 = `${S2} ${S3}`;

const TONE = {
	h: [
		"var(--hub-fill)",
		"var(--hub-stroke)",
		"var(--hub-ink)",
		"var(--on-dark-soft)",
	],
	l: [
		"var(--lnk-fill)",
		"var(--lnk-stroke)",
		"var(--lnk-ink)",
		"var(--ink-muted)",
	],
	s: [
		"var(--sal-fill)",
		"var(--sal-stroke)",
		"var(--sal-ink)",
		"var(--ink-muted)",
	],
	p: [
		"var(--pass-fill)",
		"var(--pass-stroke)",
		"var(--pass-ink)",
		"var(--ink-muted)",
	],
	n: [
		"var(--bg-canvas)",
		"var(--border-strong)",
		"var(--ink)",
		"var(--ink-muted)",
	],
} as const;

type Tone = keyof typeof TONE;

const HDRS: readonly [number, string][] = [
	[16, "STEPS 1–3 · PROJECT.SQLITE"],
	[236, "STEPS 4–5 · BROKER & GRAPH A"],
	[464, "STEP 6 · CEGAR (L1 · V1–V8)"],
	[710, "STEP 7 · 11 TARGETS + EXPLORER"],
];

const LANES: readonly [number, number][] = [
	[236, 200],
	[464, 218],
	[710, 202],
];

const EDGES: readonly [string, string][] = [
	["M 206 63 C 226 63, 224 80, 242 80", S0],
	["M 206 131 C 226 131, 224 92, 242 92", S01],
	["M 206 199 L 242 166", S12],
	["M 206 266 L 242 182", S12],
	["M 206 72 C 224 72, 222 248, 242 248", S23],
	["M 428 78 L 470 74", S01],
	["M 428 166 L 470 146", S12],
	["M 428 248 L 470 234", S23],
	["M 674 84 L 716 80", ALL],
	["M 674 220 L 716 166", ALL],
	["M 674 156 L 716 246", S23],
];

type Box = readonly [
	number,
	number,
	number,
	number,
	string,
	string,
	string,
	Tone,
];

const BOXES: readonly Box[] = [
	[
		16,
		34,
		190,
		58,
		"Step 1–3 · Scope & Map",
		"core_spine · 125-perm MinHash",
		S0,
		"s",
	],
	[
		16,
		102,
		190,
		58,
		"6 Static Connectors",
		"SQLite · DuckDB · Parquet · UC",
		S01,
		"h",
	],
	[
		16,
		170,
		190,
		58,
		"DocumentMap & Scan Gate",
		"PDF / DOCX / 1x Domain Notes",
		S12,
		"l",
	],
	[
		16,
		238,
		190,
		56,
		"LOCK-W00N + .opendv.zip",
		"Wave 2 Drift · <MASKED_PII>",
		S23,
		"s",
	],
	[
		244,
		44,
		184,
		72,
		"JobMcpBroker (/coord)",
		"Isolated /scout · FIBO BM25",
		S01,
		"h",
	],
	[
		244,
		130,
		184,
		72,
		"4-Path Grounding + Hold",
		"PATH_A1/A2 · PATH_B1/B2",
		S12,
		"s",
	],
	[
		244,
		216,
		184,
		66,
		"Step 5 · 4-Mode Graph A",
		"KeyTier · YAML CST · OCC",
		ALL,
		"p",
	],
	[
		472,
		44,
		202,
		60,
		"L1 Codemods + Triage",
		"Alg A.1–A.4 · A.9a · VaultIR",
		S01,
		"n",
	],
	[
		472,
		114,
		202,
		62,
		"V1–V6B + V7 Frontier Critic",
		"15 DV-DYN · Alg A.6 · V7 >= 90",
		S12,
		"s",
	],
	[
		472,
		186,
		202,
		96,
		"V8 Horizon + Guided UX",
		"BRK-01..08 · Bucket A/B/C · Seal",
		ALL,
		"p",
	],
	[
		718,
		44,
		186,
		72,
		"CertifiedVaultIR v2.0",
		"LOCK-W00N · AI_PROVENANCE",
		ALL,
		"p",
	],
	[
		718,
		130,
		186,
		72,
		"11 Targets · TargetEvalGate",
		"8 SQL + Iceberg/Parquet/dbt",
		ALL,
		"h",
	],
	[
		718,
		216,
		186,
		66,
		"DV Schema & Run Explorer",
		"5-Zone Z0–Z4 · Last-Event-ID",
		S23,
		"l",
	],
];

export function InferenceMatrixDiagram(): React.JSX.Element {
	return (
		<figure
			className="diagram-frame"
			data-diagram="inference-matrix"
			data-active-scenario="cold-start-w1"
			data-paused="false"
		>
			<div className="diagram-toolbar">
				<div
					className="dia-ctrl-group"
					role="tablist"
					aria-label="Execution mode scenarios"
				>
					{INFERENCE_SCENARIOS.map((s, idx) => (
						<button
							key={s.id}
							id={`scenario-tab-${s.id}`}
							type="button"
							role="tab"
							className="dia-ctrl-btn"
							data-scenario-tab={s.id}
							aria-selected={idx === 0 ? "true" : "false"}
						>
							{s.tabLabel}
						</button>
					))}
				</div>
				<div className="dia-ctrl-group">
					<button
						type="button"
						className="dia-ctrl-btn"
						data-diagram-pause="inference-matrix"
					>
						Pause
					</button>
					<button
						type="button"
						className="dia-ctrl-btn"
						data-lightbox-open="inference-matrix"
					>
						Enlarge
					</button>
				</div>
			</div>

			<div className="dia-canvas" data-lightbox-source="inference-matrix">
				<svg
					className="dia"
					viewBox="0 0 920 310"
					role="img"
					aria-labelledby="im-title im-desc"
				>
					<title id="im-title">
						7-Step Guided ⇄ Observatory &amp; Discovery Inference Matrix
					</title>
					<desc id="im-desc">
						Steps 1-3 zero-LLM profiling and DocumentMap, Steps 4-5 brokered
						scouts and 4-Path grounding into Graph A, Step 6 L1 codemods and
						V1-V8 CEGAR proofs, and Step 7 11-target 5-Zone (Z0-Z4) compilation
						+ DV Schema Explorer + Run Explorer.
					</desc>
					<defs>
						<marker
							id="im-arrow"
							viewBox="0 0 10 10"
							refX="8"
							refY="5"
							markerWidth="6"
							markerHeight="6"
							orient="auto-start-reverse"
						>
							<path d="M0,1 L9,5 L0,9 Z" fill="var(--primary-active)" />
						</marker>
					</defs>
					{HDRS.map(([x, lbl]) => (
						<text
							key={lbl}
							x={x}
							y={22}
							fontSize="10"
							fontWeight="600"
							fontFamily="var(--font-mono)"
							fill="var(--ink-muted)"
						>
							{lbl}
						</text>
					))}
					{LANES.map(([x, w]) => (
						<rect
							key={x}
							x={x}
							y="34"
							width={w}
							height="260"
							rx="8"
							fill="var(--bg-card)"
							stroke="var(--border-strong)"
						/>
					))}
					{EDGES.map(([d, sc]) => (
						<path
							key={d}
							d={d}
							fill="none"
							stroke="var(--primary-active)"
							strokeWidth="1.75"
							strokeDasharray="5 4"
							className="dia-flow"
							data-scenario-node={sc}
							markerEnd="url(#im-arrow)"
						/>
					))}
					{BOXES.map(([x, y, w, h, title, sub, sc, tone]) => {
						const [fill, stroke, ink, subInk] = TONE[tone];
						return (
							<g key={title} data-scenario-node={sc}>
								<rect
									x={x}
									y={y}
									width={w}
									height={h}
									rx="6"
									fill={fill}
									stroke={stroke}
									strokeWidth="1.25"
								/>
								<text
									x={x + 10}
									y={y + 24}
									stroke="none"
									fontSize="11"
									fontWeight="600"
									fontFamily="var(--font-mono)"
									fill={ink}
								>
									{title}
								</text>
								<text
									x={x + 10}
									y={y + 44}
									stroke="none"
									fontSize="9.5"
									fill={subInk}
								>
									{sub}
								</text>
							</g>
						);
					})}
				</svg>
			</div>

			{INFERENCE_SCENARIOS.map((s, idx) => (
				<dl
					key={s.id}
					className="scenario-readout"
					data-scenario-panel={s.id}
					hidden={idx !== 0}
				>
					{(
						[
							["Mode", s.mode, true],
							["Inputs", s.inputs, false],
							["Strategy", s.strategy, false],
							["Algorithms", s.algorithms, false],
							["Outputs", s.outputs, false],
						] as const
					).map(([k, v, isCode]) => (
						<div key={k} className="readout-row">
							<dt>{k}</dt>
							<dd>{isCode ? <code>{v}</code> : v}</dd>
						</div>
					))}
				</dl>
			))}
		</figure>
	);
}
