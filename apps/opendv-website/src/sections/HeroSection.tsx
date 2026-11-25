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
		title: "1. 7-Step Guided ⇄ Observatory",
		detail:
			"Zero-LLM project.sqlite scoping (core_spine & .opendv.zip), 6 static adapters, 125-perm MinHash clusters, DocumentMap scan gates, brokered Step 4 Holds, and 4-Mode Step 5 Graph A.",
	},
	{
		num: "02",
		title: "2. L1 + V1–V8 CEGAR & Horizon Critic",
		detail:
			"Lowers DraftVaultIR into VaultSchemaIR v2.0 via L1 codemods, V1–V5 DAG/hash/lock proofs, V6 15-invariant DuckDB prover, V6B smells, V7 Frontier critic, and V8 BRK-01..08 resilience.",
	},
	{
		num: "03",
		title: "3. 11 Targets, Explorer & Run Explorer",
		detail:
			"Compiles 8 SQL dialects + Iceberg, Parquet & dbt with TargetEvalGate parity, 4-View 5-Zone (Z0–Z4) DV Schema Explorer, Budget Meter, and Last-Event-ID Run Explorer.",
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
							with zero login friction. Brokered scouts ground{" "}
							<code>Graph A</code> across the 7-Step{" "}
							<code>Guided ⇄ Observatory</code> workflow while <code>L1</code>{" "}
							&amp; <code>V1–V8</code> CEGAR prove every invariant before
							11-target 5-Zone (Z0–Z4) <code>information_mart</code> SQL
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
