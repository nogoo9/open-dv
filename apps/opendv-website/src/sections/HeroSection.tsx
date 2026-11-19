import type React from "react";
import { SITE_META } from "../data/site-data";
import { VaultFirewallDiagram } from "../illustrations/VaultFirewallDiagram";

const HERO_PILLARS: readonly {
	readonly num: string;
	readonly title: string;
	readonly detail: string;
}[] = [
	{
		num: "01",
		title: "1. Unified 6-Step Wizard",
		detail:
			"Agent-only UI (D28) from Scope & 6 Starter Packs, Multi-Connector + OCR + Inline Notes, <= 50-row/chunk Preview, and 422 Agent Gate through Step 5 Diff Graph & Export.",
	},
	{
		num: "02",
		title: "2. 6-Gate axi-dv + 5-Zone Compiler",
		detail:
			"Auto-binds deployed_contract (D12), proves 3-Tier BKCC grain, Alg A.5 SHA-256, NetworkX DAG, and 4-Batch DuckDB before emitting 5-Zone (Z0–Z4) + information_mart SQL.",
	},
	{
		num: "03",
		title: "3. 4-View DV Schema Explorer & Live Logs",
		detail:
			"Inspect Hub-Link-Sat-PIT topology, 100% Column Triage, W1->W3 wave diffs, Safe Read-Only SQL (D11), live Cost Meter, and SessionLogsInspector EventSource traces.",
	},
];

export function HeroSection(): React.JSX.Element {
	return (
		<section
			id="top"
			className="band hero-band"
			tabIndex={-1}
			aria-labelledby="main-title"
		>
			<div className="wrap">
				<div className="split">
					<div>
						<p className="eyebrow">
							AGENT-FIRST DATA VAULT 2.0 · {SITE_META.license.toUpperCase()} ·{" "}
							{SITE_META.version}
						</p>
						<h1 id="main-title" className="display-title" tabIndex={-1}>
							Enterprise Data Vault 2.0, <em>verified</em> before a single SQL
							line runs.
						</h1>
						<p className="lede">
							Launch the 4-Tab Workbench on loopback <code>127.0.0.1:8420</code>{" "}
							with zero login friction. Agents ground semantics in the Unified
							6-Step Wizard while 6-Gate <code>axi-dv</code> proves every
							invariant before 5-Zone (Z0–Z4) <code>information_mart</code> SQL
							compiles.
						</p>

						<div className="btn-row">
							<a className="btn btn-primary" href="#workbench">
								Explore 12-Screen Tour
							</a>
							<a className="btn btn-secondary" href="#tour">
								Watch 2:45 Tour
							</a>
							<a
								className="btn btn-secondary"
								href={SITE_META.repoUrl}
								target="_blank"
								rel="noopener noreferrer"
							>
								GitHub ↗
							</a>
						</div>

						<div className="cmd-box">
							<code>$ {SITE_META.heroInstallCmd}</code>
							<button
								type="button"
								className="copy-btn"
								data-copy-text={SITE_META.heroInstallCmd}
								aria-label="Copy install and serve command"
							>
								Copy
							</button>
						</div>
						<p
							className="prose"
							style={{ fontSize: "0.84rem", marginBlockStart: "0.6rem" }}
						>
							Loopback zero-auth open access by default; non-loopback binds fail
							closed (exit 40) without{" "}
							<code>--allow-remote-unauthenticated</code>.
						</p>
					</div>

					<div>
						<VaultFirewallDiagram />
					</div>
				</div>

				<ol className="family" aria-label="Core architecture pillars">
					{HERO_PILLARS.map((pillar) => (
						<li key={pillar.num}>
							<span className="mark" aria-hidden="true">
								{pillar.num}
							</span>
							<h2 className="pillar-title">{pillar.title}</h2>
							<p>{pillar.detail}</p>
						</li>
					))}
				</ol>
			</div>
		</section>
	);
}
