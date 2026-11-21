import type React from "react";
import { AXI_GATES } from "../data/site-data";
import { SixGateVerifierDiagram } from "../illustrations/SixGateVerifierDiagram";

export function VerifierDeepDiveSection(): React.JSX.Element {
	return (
		<section
			id="verifier"
			className="band"
			tabIndex={-1}
			aria-labelledby="verifier-heading"
		>
			<div className="wrap">
				<div className="section-head">
					<div className="section-head-copy">
						<p className="eyebrow">
							SYMBOLIC FIREWALL · 6-GATE AXI-DV &amp; @TOON_V1 SELF-HEALING
						</p>
						<h2 id="verifier-heading">
							Mathematical proof before a single contract seals.
						</h2>
					</div>
					<p className="section-intro">
						Every candidate contract runs through six deterministic verification
						gates (<code>G0</code>–<code>G5</code>) and 15 dynamic sandbox
						invariants. Codemods <code>A.1</code>–<code>A.9b</code> heal
						mechanical faults; <code>@toon_v1</code> repairs structural edges,
						and Tier-U2 / Budget Holds resolve inline inside Step 6.
					</p>
				</div>

				<SixGateVerifierDiagram />

				<ol
					className="grid-3"
					style={{
						listStyle: "none",
						margin: "1.75rem 0 0",
						padding: 0,
					}}
					aria-label="Axi-DV Gates G0 to G5"
				>
					{AXI_GATES.map((gate) => (
						<li key={gate.id} className="card">
							<div className="badge-row" style={{ marginBlockEnd: "0.55rem" }}>
								<span className="badge badge-pass">{gate.id}</span>
								<span className="badge">{gate.rules}</span>
							</div>
							<h3>{gate.name}</h3>
							<p className="prose" style={{ fontSize: "0.92rem" }}>
								{gate.check}
							</p>
							<p
								className="prose"
								style={{
									fontSize: "0.86rem",
									marginBlockStart: "0.75rem",
									paddingBlockStart: "0.75rem",
									borderBlockStart: "1px solid var(--border)",
								}}
							>
								<strong>Repair:</strong> {gate.repair}
							</p>
						</li>
					))}
				</ol>
			</div>
		</section>
	);
}
