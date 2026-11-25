import type React from "react";
import { FAULT_PRESETS, VERIFIER_GATES } from "../data/site-data";

const M = "var(--font-mono)";
const PS = "var(--pass-stroke)";
const PI = "var(--pass-ink)";
const U = "var(--ink-muted)";
const I = "var(--ink)";
const HS = "var(--surface-dark)";
const CRD = "var(--bg-canvas)";

const SN =
	"Codemods & Triage|DAG, Hash & Locks|DuckDB 15-DV-DYN|Schema Smells|Frontier Critic|Horizon Critic".split(
		"|",
	);
const SR =
	"Alg A.1..A.4 · S6-03|DV-STR · A.5 · A.9b|DV-DYN · Alg A.6|DV-AP · WAIV-*|V7 >= 90/95|BRK-01..08 >= 85".split(
		"|",
	);
const BATCHES = [
	[622, 222, "Ghost 0*64 + -1/-2"],
	[762, 222, "Replay delta = 0"],
	[622, 254, "Boundary-Shift A.5"],
	[762, 254, "BKCC + Soft Purity"],
] as const;

const bx = (
	x: number,
	y: number,
	w: number,
	h: number,
	f: string,
	s: string,
	rx = 6,
) => (
	<rect
		x={x}
		y={y}
		width={w}
		height={h}
		rx={rx}
		fill={f}
		stroke={s}
		strokeWidth="1.25"
	/>
);
const tx = (x: number, y: number, s: string, sz = 10, c = I, m = 1) => (
	<text
		x={x}
		y={y}
		fontSize={sz}
		fontWeight={m ? 600 : 400}
		fontFamily={m ? M : undefined}
		fill={c}
	>
		{s}
	</text>
);
const ln = (d: string, c: string, cls?: string) => (
	<path
		key={d}
		d={d}
		fill="none"
		stroke={c}
		strokeWidth="2"
		strokeDasharray={cls ? "4 3" : undefined}
		className={cls}
		markerEnd="url(#sg-arr)"
	/>
);

export function SixGateVerifierDiagram(): React.JSX.Element {
	return (
		<figure
			className="diagram-frame"
			data-diagram="six-gate-verifier"
			data-active-gate="L1"
			data-active-fault="none"
			data-paused="false"
		>
			<div className="diagram-toolbar">
				<div className="dia-ctrl-group">
					<button
						type="button"
						className="dia-ctrl-btn"
						data-fault-btn="none"
						aria-pressed="true"
					>
						Normal CEGAR Loop
					</button>
					{FAULT_PRESETS.map((f) => (
						<button
							key={f.id}
							type="button"
							className="dia-ctrl-btn"
							data-fault-btn={f.id}
							aria-pressed="false"
						>
							{f.label}
						</button>
					))}
				</div>
				<div className="dia-ctrl-group">
					<button
						type="button"
						className="dia-ctrl-btn"
						data-diagram-pause="six-gate-verifier"
					>
						Pause
					</button>
					<button
						type="button"
						className="dia-ctrl-btn"
						data-lightbox-open="six-gate-verifier"
					>
						Enlarge
					</button>
				</div>
			</div>
			<div className="dia-canvas" data-lightbox-source="six-gate-verifier">
				<svg
					className="dia"
					viewBox="0 0 920 300"
					role="img"
					aria-labelledby="sg-title sg-desc"
					stroke="none"
				>
					<title id="sg-title">Open-DV V2 6-Gate CEGAR Verifier</title>
					<desc id="sg-desc">
						L1 deterministic codemods, V1_V5 symbolic DAG/hashing/spans, V6
						DuckDB 15-invariant prover, V6B schema smells, V7 semantic critic,
						and V8 horizon resilience critic with bounded L2/L3 repair.
					</desc>
					<defs>
						<marker
							id="sg-arr"
							viewBox="0 0 10 10"
							refX="8"
							refY="5"
							markerWidth="6"
							markerHeight="6"
							orient="auto-start-reverse"
						>
							<path d="M0 1L9 5L0 9Z" fill="context-stroke" />
						</marker>
					</defs>
					{ln("M114 130L136 130", PS, "dia-flow")}
					{ln(
						"M583 168C583 244 65 244 65 170",
						"var(--primary-active)",
						"dia-heal-arc",
					)}
					{ln("M453 168L453 200", PS)}
					{ln("M610 238L583 238", PS)}
					{bx(148, 14, 612, 44, HS, HS)}
					{bx(16, 92, 98, 76, "var(--bg-card)", "var(--border-strong)")}
					{bx(
						170,
						214,
						290,
						46,
						"var(--surface-cream-strong)",
						"var(--primary)",
					)}
					{bx(610, 190, 294, 96, "var(--pass-fill)", PS)}
					<g textAnchor="middle">
						{tx(
							454,
							33,
							"Deterministic L1 Codemods · Alg A.1 / A.4 / A.5 / A.9a",
							11,
							"var(--on-dark)",
						)}
						{tx(
							454,
							49,
							"Zero-LLM IR enrichment fixes sparse FKs, _ext_vN drift & triage",
							9,
							"var(--on-dark-soft)",
							0,
						)}
						{tx(65, 120, "@ship-vault", 11, I)}
						{tx(65, 138, "DraftVaultIR", 9, U, 0)}
						{tx(
							315,
							233,
							"L2 Bounded Repair (K_max) & L3 Guided Hold",
							10.5,
							"var(--primary-active)",
						)}
						{tx(
							315,
							250,
							"E(IR_k) = 100*|V1..V6| + 10*|V6B| + max(0, floor - V7)",
							8.5,
							U,
							0,
						)}
						{tx(757, 210, "V6 :memory: DuckDbProverAdapter", 10.5, PI)}
					</g>
					{VERIFIER_GATES.map((g, i) => {
						const gx = 138 + i * 130;
						const badgeW = g.id.length > 3 ? 44 : 34;
						return (
							<g key={g.id}>
								{i > 0 && ln(`M${gx - 20} 130L${gx - 2} 130`, PS, "dia-flow")}
								{i > 0 &&
									i < 5 &&
									ln(`M${gx + 55} 92L${gx + 55} 60`, HS, "dia-flow")}
								<g
									className="dia-gate-node"
									data-gate-select={g.id}
									// biome-ignore lint/a11y/useSemanticElements: SVG group node acts as interactive gate button inside inline SVG
									role="button"
									tabIndex={0}
									aria-pressed={g.id === "L1" ? "true" : "false"}
									stroke="var(--border-strong)"
									strokeWidth="1.5"
								>
									<rect
										x={gx}
										y="92"
										width="110"
										height="76"
										rx="7"
										className="dia-gate-box"
										fill={CRD}
									/>
									{bx(gx + 8, 100, badgeW, 18, "var(--pass-fill)", PS, 4)}
									<g stroke="none" textAnchor="middle">
										{tx(gx + 8 + badgeW / 2, 113, g.id, 9.5, PI)}
										<circle
											cx={gx + 96}
											cy="109"
											r="3.5"
											className="dia-pulse"
											fill={PS}
										/>
										{tx(gx + 55, 136, SN[i] ?? g.name, 10.5, I, 0)}
										{tx(gx + 55, 155, SR[i] ?? g.rules, 8.5, U)}
									</g>
								</g>
							</g>
						);
					})}
					<g textAnchor="middle">
						{BATCHES.map(([x0, y0, lbl]) => (
							<g key={lbl}>
								{bx(x0, y0, 130, 24, CRD, PS, 4)}
								{tx(x0 + 65, y0 + 15, lbl, 9, PI)}
							</g>
						))}
					</g>
				</svg>
			</div>
			{VERIFIER_GATES.map((g) => (
				<div
					key={g.id}
					className="gate-readout"
					data-gate-detail={g.id}
					hidden={g.id !== "L1"}
				>
					<strong>
						{g.id} · {g.name}
					</strong>
					<dl className="gate-readout-dl">
						<div className="readout-row">
							<dt>Rules</dt>
							<dd>
								<code>{g.rules}</code>
							</dd>
						</div>
						<div className="readout-row">
							<dt>Check</dt>
							<dd>{g.check}</dd>
						</div>
						<div className="readout-row">
							<dt>Deterministic / Agent Repair</dt>
							<dd>{g.repair}</dd>
						</div>
					</dl>
				</div>
			))}
			<div className="fault-console" aria-live="polite">
				<div className="fault-detail" data-fault-detail="none">
					All 6 CEGAR gates (L1, V1_V5, V6, V6B, V7, V8) + DuckDbProverAdapter
					nominal (E = 0).
				</div>
				{FAULT_PRESETS.map((f) => (
					<div
						key={f.id}
						className="fault-detail"
						data-fault-detail={f.id}
						hidden
					>
						<p className="fault-meta">
							<strong>Target Gate:</strong> {f.gateId} · {f.label}
						</p>
						<p>
							<strong>Symptom:</strong> {f.symptom}
						</p>
						<p>
							<strong>Resolution:</strong> {f.resolution}
						</p>
						<code className="trace-line">{f.traceSnippet}</code>
					</div>
				))}
			</div>
		</figure>
	);
}
