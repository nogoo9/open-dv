import type React from "react";

const M = "var(--font-mono)";
const I = "var(--ink)";
const U = "var(--ink-muted)";
const G = "var(--primary-active)";
const PS = "var(--pass-stroke)";
const PI = "var(--pass-ink)";
const DS = "var(--danger-stroke)";
const DI = "var(--danger-ink)";
const B = "var(--border-strong)";
const C = "var(--bg-canvas)";

const bx = (
	x: number,
	y: number,
	w: number,
	h: number,
	f = C,
	s = B,
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
const tx = (x: number, y: number, s: string, f = I, sz = 10) => (
	<text x={x} y={y} fontSize={sz} fontWeight={sz > 9 ? 600 : 400} fill={f}>
		{s}
	</text>
);
const ln = (d: string, c: string, cls?: string) => (
	<path
		d={d}
		fill="none"
		stroke={c}
		strokeWidth="2"
		strokeDasharray={cls ? "5 4" : undefined}
		className={cls}
		markerEnd="url(#vf-arr)"
	/>
);

const SRCS = [
	["SQLite · PRAGMA", "am_sec_treasury_ibor"],
	["Databricks UC · Tags", "uc://am_prod.ibor.*"],
	["Parquet / CSV · Vectorized", "Drift S(t) -> S(t+1)"],
	["PDF + Notes · LiteParse", "10-K · [char_start, end]"],
] as const;

const CORE = [
	[
		156,
		86,
		"var(--bg-card)",
		"var(--primary)",
		I,
		U,
		"@term-scout -> @ship-vault",
		"Agent-only · YAML only",
	],
	[
		262,
		56,
		"var(--surface-dark)",
		"var(--surface-dark)",
		"var(--on-dark)",
		"var(--on-dark-soft)",
		"vault_blueprint.yaml",
		"3-Tier BKCC · Canonical IR",
	],
	[
		338,
		136,
		"var(--pass-fill)",
		PS,
		PI,
		PI,
		"axi-dv 6-Gate (G0–G5)",
		"Alg A.1–A.9b · 4-Batch",
	],
] as const;

const NODES = [
	["[HUB]", "hub_security", "hub", "var(--hub-ink)", 8],
	["[LNK]", "lnk_holding", "lnk", I, 19],
	["[PIT]", "pit_position", "sal", I, 6],
	["[SAT-PII]", "sat_investor_pii", "pii", I, 6],
] as const;

export function VaultFirewallDiagram(): React.JSX.Element {
	return (
		<figure
			className="diagram-frame"
			data-diagram="vault-firewall"
			data-firewall-mode="opendv"
			data-paused="false"
		>
			<div className="diagram-toolbar">
				<div className="dia-ctrl-group">
					<button
						type="button"
						className="dia-ctrl-btn"
						data-firewall-select="opendv"
						aria-pressed="true"
					>
						Open-DV Neuro-Symbolic
					</button>
					<button
						type="button"
						className="dia-ctrl-btn"
						data-firewall-select="naive"
						aria-pressed="false"
					>
						Naive LLM Raw SQL
					</button>
				</div>
				<div className="dia-ctrl-group">
					<button
						type="button"
						className="dia-ctrl-btn"
						data-diagram-pause="vault-firewall"
					>
						Pause
					</button>
					<button
						type="button"
						className="dia-ctrl-btn"
						data-lightbox-open="vault-firewall"
					>
						Enlarge
					</button>
				</div>
			</div>
			<div className="dia-canvas" data-lightbox-source="vault-firewall">
				<svg
					className="dia"
					viewBox="0 0 760 490"
					role="img"
					aria-labelledby="vf-title vf-desc"
					stroke="none"
					fontFamily={M}
				>
					<title id="vf-title">Open-DV Neuro-Symbolic Vault Firewall</title>
					<desc id="vf-desc">
						Open-DV agents emit vault_blueprint.yaml through 6-Gate axi-dv
						(G0-G5) and @toon_v1 into 5-Zone (Z0-Z4) Data Vault 2.0 SQL vs
						blocked Naive LLM SQL.
					</desc>
					<defs>
						<marker
							id="vf-arr"
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
					{tx(16, 26, "#1 ENTERPRISE SOURCES", U)}
					{tx(236, 26, "#2 NEURO-SYMBOLIC CORE", U)}
					{tx(564, 26, "#3 VERIFIED 5-ZONE VAULT", U)}
					<g className="dia-sources-zone">
						{bx(16, 40, 174, 434, "var(--bg-card)", B, 8)}
						{SRCS.map(([title, sub], i) => {
							const y = 56 + i * 94;
							return (
								<g key={title}>
									{bx(26, y, 154, 78)}
									{tx(36, y + 26, title, I, 11)}
									{i === 2 && (
										<circle
											cx="42"
											cy={y + 49}
											r="3.5"
											className="dia-pulse"
											fill={G}
										/>
									)}
									{tx(i === 2 ? 51 : 36, y + 52, sub, i === 2 ? G : U, 9)}
								</g>
							);
						})}
						{tx(34, 452, "Graph B + Char Spans", I)}
					</g>
					<g className="dia-naive-branch">
						{ln("M190 88L234 88", DS, "dia-flow")}
						{ln("M466 88L494 88", DS, "dia-flow")}
						{bx(236, 40, 230, 96, "var(--danger-fill)", DS, 8)}
						{tx(250, 62, "Naive LLM Direct SQL", DI, 12)}
						{tx(250, 82, "Raw DDL without proof", U)}
						{tx(250, 112, "CONCAT_WS('|',a,b) / UPDATE", DI, 9)}
						{bx(496, 40, 248, 96, "var(--danger-fill)", DS, 8)}
						{tx(510, 66, "[BLOCKED] · 5 Fatal Traps", DI, 11)}
						{tx(510, 94, "• Surrogate Leaks · Hash Collisions", DI)}
						{tx(510, 115, "• UPDATEs · Phantom Links", DI)}
					</g>
					<g className="dia-opendv-branch">
						{ln("M190 198L234 198", PS, "dia-flow")}
						{ln("M385 242L385 260", PS)}
						{ln("M385 318L385 336", PS)}
						{ln("M236 406C202 406 202 222 234 222", G, "dia-heal-arc")}
						{ln("M534 406L562 406", PS, "dia-flow")}
						{CORE.map(([y, h, f, s, tc, sc, t1, t2]) => (
							<g key={t1}>
								{bx(236, y, 298, h, f, s, 7)}
								{tx(248, y + 22, t1, tc, 11)}
								{tx(248, y + 41, t2, sc, 9)}
							</g>
						))}
						{bx(188, 300, 56, 22, "var(--surface-cream-strong)", G, 4)}
						{tx(193, 314, "@toon_v1", G, 9)}
						{bx(564, 156, 180, 318, C, PS, 8)}
						{tx(576, 179, "Bipartite Constellation", PI, 11)}
						{bx(
							578,
							404,
							154,
							58,
							"var(--surface-dark)",
							"var(--surface-dark)",
						)}
						{tx(588, 426, "5-Zone (Z0–Z4) SQL", "var(--on-dark)")}
						{tx(
							588,
							446,
							"information_mart · SHA-256",
							"var(--accent-teal)",
							9,
						)}
						<g textAnchor="middle">
							{"012345".split("").map((n, i) => {
								const gx = 248 + i * 46;
								return (
									<g key={n}>
										{bx(gx, 396, 42, 44, C, PS, 5)}
										<circle
											cx={gx + 34}
											cy="404"
											r="2.5"
											className="dia-pulse"
											fill={PS}
										/>
										{tx(gx + 21, 424, `G${n}`, PI, 11)}
									</g>
								);
							})}
						</g>
						{NODES.map(([role, name, k, nameInk, rx], i) => {
							const ny = 196 + i * 52;
							return (
								<g key={name}>
									{bx(
										578,
										ny,
										154,
										38,
										`var(--${k}-fill)`,
										`var(--${k}-stroke)`,
										rx,
									)}
									{tx(
										586,
										ny + 15,
										role,
										k === "hub" ? "var(--accent-amber)" : `var(--${k}-ink)`,
										9,
									)}
									{tx(586, ny + 30, name, nameInk, 10)}
								</g>
							);
						})}
					</g>
				</svg>
			</div>
			<figcaption data-firewall-caption>
				Open-DV enforces semantic Agents + deterministic 6-Gate Firewall before
				any 5-Zone (Z0–Z4) SQL compiles.
			</figcaption>
		</figure>
	);
}
