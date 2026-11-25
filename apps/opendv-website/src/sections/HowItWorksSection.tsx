import type React from "react";
import { PIPELINE_STAGES, type PipelineStage } from "../data/site-data";
import { InferenceMatrixDiagram } from "../illustrations/InferenceMatrixDiagram";

const PIPELINE_PHASES: readonly {
	readonly phase: PipelineStage["phase"];
	readonly badgeClass: string;
	readonly subtitle: string;
}[] = [
	{
		phase: "Scope & Evidence",
		badgeClass: "badge badge-hub",
		subtitle:
			"5-table project.sqlite, 6 static adapters, MinHash clusters & scan consent",
	},
	{
		phase: "Discovery & Ontology",
		badgeClass: "badge badge-sat",
		subtitle:
			"JobMcpBroker (/coord vs /scout), 4-Path grounding & 4-Mode Graph A",
	},
	{
		phase: "CEGAR & Critics",
		badgeClass: "badge badge-pass",
		subtitle:
			"S6-01..S6-07: L1 codemods, V1–V6B prover, V7 semantic & V8 Horizon Critic",
	},
	{
		phase: "11-Target Storage",
		badgeClass: "badge badge-lnk",
		subtitle:
			"8 SQL dialects + Iceberg/Parquet/dbt across 5 Zones (Z0–Z4) + information_mart",
	},
];

const GROUNDING_DISTINCTIONS: readonly {
	readonly badge: string;
	readonly badgeClass: string;
	readonly title: string;
	readonly detail: string;
}[] = [
	{
		badge: "PATH_A1 · skos:exactMatch",
		badgeClass: "badge badge-pass",
		title: "Canonical Business Concepts vs. Matched FIBO Classes",
		detail:
			"Graph A concepts map to Hub tables (hub_*) while FIBO-core BM25 (>= 0.6500) anchors standardized financial semantics without forcing premature renames.",
	},
	{
		badge: "PATH_A2 · skos:closeMatch",
		badgeClass: "badge badge-lnk",
		title: "Approximate FIBO Nuance Review",
		detail:
			"Highlights domain-narrower concepts (e.g., TreasuryRate or ComplianceBreachAlert) for side-by-side Steward inspection in the 716px Citation & FIBO Drawer.",
	},
	{
		badge: "PATH_B1 · Beyond-FIBO",
		badgeClass: "badge badge-sal",
		title: "Empirical Table & 1x Document Grounding",
		detail:
			"Novel proprietary or 1x-mentioned SLA rules (e.g., EsgRating, StewardshipEngagementCase) pass cleanly when bound to Graph B columns or [char_start, char_end] spans.",
	},
	{
		badge: "PATH_B2 · Fail-Closed",
		badgeClass: "badge badge-risk",
		title: "Quarantined Candidates & Signed Waivers",
		detail:
			"Ungrounded candidates stay quarantined from commit until bound to concrete evidence or signed off with a >= 20-character Steward waive_quarantine rationale.",
	},
];

export function HowItWorksSection(): React.JSX.Element {
	return (
		<section
			id="how"
			className="band band-soft"
			tabIndex={-1}
			aria-labelledby="how-heading"
		>
			<div className="wrap">
				<div className="section-head">
					<div className="section-head-copy">
						<p className="eyebrow">
							HOW IT WORKS · 7-STEP GUIDED ⇄ OBSERVATORY &amp; CEGAR
						</p>
						<h2 id="how-heading">
							One unified workflow from Scope to 11-Target 5-Zone SQL.
						</h2>
					</div>
					<p className="section-intro">
						Steps 1–5 scope <code>project.sqlite</code> baselines, attach 6
						static adapters, inspect <code>125</code>-permutation{" "}
						<code>MinHash</code> clusters, run privilege-separated Step 4
						scouts, and commit <code>Graph A</code> via 4-Mode Explorer. Step 6
						pairs plain-English <code>Guided</code> Decision Cards with 1-click{" "}
						<code>Architect Observatory</code> (<code>S6-01..S6-07</code>,{" "}
						<code>LOCK-W00N</code>) before Step 7 compiles all 11 targets.
					</p>
				</div>

				<InferenceMatrixDiagram />

				<div className="grid-4" style={{ marginBlockStart: "1.75rem" }}>
					{PIPELINE_PHASES.map(({ phase, badgeClass, subtitle }) => {
						const stages = PIPELINE_STAGES.filter((s) => s.phase === phase);
						return (
							<article key={phase} className="card">
								<div className="badge-row" style={{ marginBlockEnd: "0.5rem" }}>
									<span className={badgeClass}>{phase.toUpperCase()}</span>
								</div>
								<h3>{phase}</h3>
								<p className="prose" style={{ fontSize: "0.9rem" }}>
									{subtitle}
								</p>
								<ol
									className="trap-list"
									style={{ marginBlockStart: "0.9rem" }}
								>
									{stages.map((stage) => (
										<li key={stage.id} className="trap-item">
											<div
												className="badge-row"
												style={{ marginBlockEnd: "0.25rem" }}
											>
												<span className="badge">{stage.id}</span>
												<span className="badge badge-gold">{stage.temp}</span>
											</div>
											<strong>{stage.actor}</strong>
											<p style={{ marginBlockEnd: "0.25rem" }}>
												<code>{stage.code}</code>
											</p>
											<p>{stage.summary}</p>
										</li>
									))}
								</ol>
							</article>
						);
					})}
				</div>

				<div className="grid-4" style={{ marginBlockStart: "1.5rem" }}>
					{GROUNDING_DISTINCTIONS.map((item) => (
						<article key={item.badge} className="card-subtle">
							<div className="badge-row" style={{ marginBlockEnd: "0.5rem" }}>
								<span className={item.badgeClass}>{item.badge}</span>
							</div>
							<h3 style={{ fontSize: "1rem" }}>{item.title}</h3>
							<p className="prose" style={{ fontSize: "0.86rem" }}>
								{item.detail}
							</p>
						</article>
					))}
				</div>
			</div>
		</section>
	);
}
