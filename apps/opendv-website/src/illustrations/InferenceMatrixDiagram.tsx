import type React from "react";
import { INFERENCE_SCENARIOS } from "../data/site-data";

const S0 = "day0-seed";
const S1 = "sqlite-greenfield";
const S2 = "hybrid-drift";
const S3 = "verified-noop";
const ALL = `${S0} ${S1} ${S2} ${S3}`;
const S01 = `${S0} ${S1}`;
const S12 = `${S1} ${S2}`;
const S023 = `${S0} ${S2} ${S3}`;

const TONE = {
	h: ["var(--hub-fill)", "var(--hub-stroke)", "var(--hub-ink)"],
	l: ["var(--lnk-fill)", "var(--lnk-stroke)", "var(--lnk-ink)"],
	s: ["var(--sat-fill)", "var(--sat-stroke)", "var(--sat-ink)"],
	p: ["var(--pass-fill)", "var(--pass-stroke)", "var(--pass-ink)"],
	n: ["var(--bg-card)", "var(--border-strong)", "var(--ink)"],
} as const;

type Tone = keyof typeof TONE;

const HDRS: readonly [number, string][] = [
	[16, "ONTOLOGY + CONNECTORS"],
	[236, "mode_detector.py"],
	[464, "CODEMODS & GATES G0-G5"],
	[710, "SEALED OUTPUTS"],
];

const LANES: readonly [number, number][] = [
	[236, 200],
	[464, 218],
	[710, 202],
];

const EDGES: readonly [string, string][] = [
	["M 206 63 C 226 63, 224 80, 242 80", S0],
	["M 206 131 C 226 131, 224 92, 242 92", S1],
	["M 206 199 L 242 166", S2],
	["M 206 266 L 242 182", S2],
	["M 206 72 C 224 72, 222 248, 242 248", S3],
	["M 428 78 L 470 74", S01],
	["M 428 166 L 470 146", S2],
	["M 428 248 L 470 234", S3],
	["M 674 84 L 716 80", ALL],
	["M 674 220 L 716 166", ALL],
	["M 674 156 L 716 246", S2],
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
	[16, 34, 190, 58, "Ontology Studio + CAS", "FIBO BM25 · Graph A", S023, "s"],
	[16, 102, 190, 58, "DIRECT_DUCKDB_ATTACH", "SQLite PRAGMA · Files", S12, "h"],
	[16, 170, 190, 58, "REMOTE_PUSHDOWN", "Databricks UC · Arrow", S2, "l"],
	[16, 238, 190, 56, "DOCUMENT_SPAN", "PDF Filings · LiteParse", S2, "s"],
	[244, 44, 184, 72, "COLD_START", "COLD_START_N1 (null)", S01, "h"],
	[244, 130, 184, 72, "HYBRID_PER_SOURCE", "UC + Drift + PDF", S2, "s"],
	[244, 216, 184, 66, "VERIFIED_NO_OP", "0 sources · Replay", S3, "p"],
	[
		472,
		44,
		202,
		60,
		"FIBO Seed & 3-Pass CoT",
		"@ship-vault · Graph B",
		S01,
		"n",
	],
	[472, 114, 202, 62, "Alg A.7 / A.9a / A.9b", "Lock · Drift · Spans", S2, "s"],
	[472, 186, 202, 96, "Alg A.1-A.6 & G0-G5", "Codemods + Sandbox", ALL, "p"],
	[718, 44, 186, 72, "Sealed Contract v+1", "VaultBlueprint · IR", ALL, "p"],
	[718, 130, 186, 72, "4-Zone SQL Bundle", "DuckDB + Delta", ALL, "h"],
	[718, 216, 186, 66, "15 Ontology Deltas", "Rev 2 + _ext Sats", S2, "l"],
];

export function InferenceMatrixDiagram(): React.JSX.Element {
	return (
		<figure
			className="diagram-frame"
			data-diagram="inference-matrix"
			data-active-scenario="day0-seed"
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
						End-to-End Single-Run Data-Driven Inference Pipeline
					</title>
					<desc id="im-desc">
						Ontology Studio + CAS and Source Connectors (DIRECT_DUCKDB_ATTACH,
						REMOTE_PUSHDOWN, DOCUMENT_SPAN) into mode_detector (COLD_START,
						HYBRID_PER_SOURCE, VERIFIED_NO_OP), Alg A.1-A.9b codemods and G0-G5,
						and Sealed Contract v+1 + 4-Zone SQL.
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
							<path d="M0,1 L9,5 L0,9 Z" fill="var(--gold)" />
						</marker>
					</defs>
					{HDRS.map(([x, lbl]) => (
						<text
							key={lbl}
							x={x}
							y={22}
							fontSize="10"
							fontWeight="700"
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
							fill="var(--bg-subtle)"
							stroke="var(--border-strong)"
						/>
					))}
					{EDGES.map(([d, sc]) => (
						<path
							key={d}
							d={d}
							fill="none"
							stroke="var(--gold)"
							strokeWidth="1.75"
							strokeDasharray="5 4"
							className="dia-flow"
							data-scenario-node={sc}
							markerEnd="url(#im-arrow)"
						/>
					))}
					{BOXES.map(([x, y, w, h, title, sub, sc, tone]) => {
						const [fill, stroke, ink] = TONE[tone];
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
									fontSize="11.5"
									fontWeight="700"
									fontFamily="var(--font-mono)"
									fill={ink}
								>
									{title}
								</text>
								<text
									x={x + 10}
									y={y + 44}
									stroke="none"
									fontSize="10"
									fill="var(--ink-muted)"
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
