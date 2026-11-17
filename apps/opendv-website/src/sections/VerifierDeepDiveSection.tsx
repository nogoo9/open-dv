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
						gates (<code>G0</code>–<code>G5</code>). Mechanical violations
						trigger Algorithms <code>A.1</code>–<code>A.9b</code> codemods
						automatically; structural errors emit compact <code>@toon_v1</code>{" "}
						repair traces.
					</p>
				</div>

				<SixGateVerifierDiagram />

				<ol
					className="grid-3"
					style={{
						listStyle: "none",
						margin: "1.5rem 0 0",
						padding: 0,
					}}
					aria-label="Axi-DV Gates G0 to G5"
				>
					{AXI_GATES.map((gate) => (
						<li key={gate.id} className="card">
							<div className="badge-row" style={{ marginBlockEnd: "0.45rem" }}>
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
									marginBlockStart: "0.65rem",
									paddingBlockStart: "0.65rem",
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
