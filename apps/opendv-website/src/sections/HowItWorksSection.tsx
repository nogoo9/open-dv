import type React from "react";
import { PIPELINE_STAGES, type PipelineStage } from "../data/site-data";
import { InferenceMatrixDiagram } from "../illustrations/InferenceMatrixDiagram";

const PIPELINE_PHASES: readonly {
	readonly phase: PipelineStage["phase"];
	readonly badgeClass: string;
	readonly subtitle: string;
}[] = [
	{
		phase: "Discovery",
		badgeClass: "badge badge-hub",
		subtitle:
			"Graph A grounding, relational profiling & document span extraction",
	},
	{
		phase: "Modelling",
		badgeClass: "badge badge-sat",
		subtitle:
			"3-Pass Chain-of-Thought YAML contract synthesis (one job/session)",
	},
	{
		phase: "Verification",
		badgeClass: "badge badge-pass",
		subtitle: "Deterministic static gates, codemods & DuckDB shadow sandbox",
	},
	{
		phase: "Compilation",
		badgeClass: "badge badge-lnk",
		subtitle:
			"Canonical IR, VaultBlueprint & 5-Zone (Z0–Z4) + information_mart",
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
							HOW IT WORKS · UNIFIED 6-STEP WIZARD &amp; 7-PILL CONSOLE
						</p>
						<h2 id="how-heading">
							One unified workflow from Scope to 5-Zone SQL.
						</h2>
					</div>
					<p className="section-intro">
						Steps 1–5 guide baseline scope, connectors, &le; 50-row preview (or{" "}
						<code>Fast-Forward to Step 6</code> /{" "}
						<code>Export Ontology Only</code>), Agent synthesis, and optional{" "}
						<code>VisualEditorDrawer</code>. Step 6 auto-binds{" "}
						<code>deployed_contract</code>, enforces one active job per session
						(<code>409</code> guard), and drives the 7-pill <code>00..06</code>{" "}
						console over <code>STAGE_00..07</code>.
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
			</div>
		</section>
	);
}
