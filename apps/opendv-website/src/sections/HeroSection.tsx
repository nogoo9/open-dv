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
		title: "1. Ontology Studio",
		detail:
			"Ground domain concepts against EDM Council FIBO, resolve clarification Q&A, and commit immutable CAS VaultBlueprints.",
	},
	{
		num: "02",
		title: "2. Data-Driven Pipeline",
		detail:
			"Connect SQLite, Databricks UC, DuckDB, Parquet/CSV, and PDF filings. Actions are inferred per source with zero manual waves.",
	},
	{
		num: "03",
		title: "3. 6-Gate axi-dv Compiler",
		detail:
			"Prove 3-Tier BKCC grain, injective SHA-256 hashing, NetworkX DAG topology, and 4-Batch DuckDB execution before SQL emits.",
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
							NEURO-SYMBOLIC DATA VAULT 2.0 · {SITE_META.license.toUpperCase()}{" "}
							· {SITE_META.version}
						</p>
						<h1 id="main-title" className="display-title" tabIndex={-1}>
							Enterprise Data Vault 2.0, <em>verified</em> before a single SQL
							line runs.
						</h1>
						<p className="lede">
							Agents discover enterprise semantics and propose declarative{" "}
							<code>VaultBlueprint</code> contracts. The deterministic 6-gate{" "}
							<code>axi-dv</code> verifier and DuckDB shadow sandbox prove every
							invariant before multi-dialect 4-Zone SQL compiles.
						</p>

						<div className="btn-row">
							<a className="btn btn-primary" href="#tour">
								Watch 2:45 Tour
							</a>
							<a className="btn btn-secondary" href="#quickstart">
								Quick Start →
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
